from attrs import field, define
from os.path import expanduser, join
import numpy as np
import pandas as pd
from pathlib import Path
import logging
from py_factor_graph.variables import PoseVariable2D, LandmarkVariable2D
from py_factor_graph.measurements import (
    PoseMeasurement2D,
    FGRangeMeasurement,
)
from py_factor_graph.utils.matrix_utils import (
    make_transformation_matrix_from_theta,
    make_transformation_matrix_from_rpy,
    get_relative_rot_and_trans_between_poses,
    get_theta_from_rotation_matrix,
    get_measurement_precisions_from_covariances,
)
from py_factor_graph.factor_graph import (
    FactorGraphData,
)

import logging, coloredlogs

logger = logging.getLogger(__name__)
field_styles = {
    "filename": {"color": "green"},
    "filename": {"color": "green"},
    "levelname": {"bold": True, "color": "black"},
    "name": {"color": "blue"},
}
coloredlogs.install(
    level="INFO",
    fmt="[%(filename)s:%(lineno)d] %(name)s %(levelname)s - %(message)s",
    field_styles=field_styles,
)


def _verify_path_is_goats_csv(instance, attribute, path: Path):
    assert isinstance(path, Path), "path must be a Path object"
    if not path.exists():
        raise ValueError(f"{path} does not exist")
    if not path.is_file():
        raise ValueError(f"{path} is not a file")
    if not path.suffix == ".csv":
        raise ValueError(f"{path} is not a .csv file")


@define
class GoatsParser:

    data_file_path: Path = field(validator=_verify_path_is_goats_csv)
    beacon_loc_file_path: Path = field(validator=_verify_path_is_goats_csv)
    dim: int = field(validator=lambda i, a, v: v in [2, 3])  # type: ignore
    filter_ranges: bool = field()

    _data = field(init=False)
    _beacon_locs = field(init=False)
    pyfg = field(init=False)

    def __attrs_post_init__(self):
        logger.info(f"Loading data from {self.data_file_path}")
        logger.info(f"Loading beacon locations from {self.beacon_loc_file_path}")

        # read in the sensor data
        self._data = pd.read_csv(self.data_file_path)

        # Read in the beacon locations as numpy arrays
        _beacon_loc_df = pd.read_csv(self.beacon_loc_file_path, header=None)
        assert isinstance(_beacon_loc_df, pd.DataFrame)
        self._beacon_locs = [
            _beacon_loc_df.iloc[: self.dim, idx].to_numpy()
            for idx in range(len(_beacon_loc_df.columns))
        ]

        self._check_beacon_num_consistent()

        self.pyfg = FactorGraphData(dimension=self.dim)
        self._fill_factor_graph()

    def _check_beacon_num_consistent(self):
        """
        Check that the number of beacons in the data matches the number of beacons in the beacon locations file
        """
        num_beacon_locs = len(self._beacon_locs)
        data_col_names = self._data.columns
        range_cols = [
            x for x in data_col_names if x.startswith("ranges_") and "filtered" not in x
        ]
        filtered_range_cols = [
            x for x in data_col_names if x.startswith("filtered_ranges_")
        ]
        assert len(range_cols) == len(filtered_range_cols) == num_beacon_locs, (
            f"Number of beacon info is inconsistent. "
            f"{len(range_cols)} vs {len(filtered_range_cols)} vs {num_beacon_locs}"
        )

    def _fill_factor_graph(self):
        self._add_beacon_variables()
        self._add_pose_variables()
        self._add_odometry_measurements()
        self._add_range_measurements()

    def _remap_beacon_idx(self, idx) -> int:
        logger.warning("Beacon indices are being remapped")
        if idx == 0:
            return 2
        elif idx == 1:
            return 3
        elif idx == 2:
            return 0
        elif idx == 3:
            return 1
        else:
            raise ValueError(f"Invalid beacon idx {idx}")

    def _add_beacon_variables(self):
        for idx, beacon_loc in enumerate(self._beacon_locs):
            ranges = self._get_ranges(idx)

            # if this beacon has no ranges, then we don't add it to the factor
            # graph
            num_non_nan = np.count_nonzero(~np.isnan(ranges))
            if num_non_nan < self.dim:
                logger.warning(
                    f"Beacon {idx} has only {num_non_nan} ranges, so its position "
                    "is not uniquely defined and it is not added to the factor graph"
                )
                continue

            var_name = f"L{idx}"
            var = LandmarkVariable2D(var_name, beacon_loc)
            self.pyfg.add_landmark_variable(var)

    def _add_pose_variables(self):
        for idx, pose in enumerate(zip(self.gt_positions, self.gt_rotations)):
            if not self._row_has_range_measures(idx) and idx != 0:
                logger.debug(
                    f"Pose {idx} has no range measurements, so it is not added"
                )
                continue

            position, rot = pose
            var_name = f"A{idx}"
            var = PoseVariable2D(var_name, tuple(position), rot)
            self.pyfg.add_pose_variable(var)

    def _get_transformation_matrix(self, rot, trans):
        if self.dim == 2:
            assert np.isreal(rot), f"Rot must be a number {rot}"
            assert (
                len(trans) == 2
            ), f"Translation is wrong dimension; should be 2 but is {len(trans)}"
            return make_transformation_matrix_from_theta(rot, trans)
        elif self.dim == 3:
            assert (
                len(rot) == len(trans) == 3
            ), f"Dimension mismatch: rpy = {len(rot)} and trans = {len(trans)}"
            return make_transformation_matrix_from_rpy(rot, trans)
        else:
            raise ValueError()

    def _row_has_range_measures(self, row: int) -> bool:
        beacon_ranges = (self._get_ranges(idx) for idx in range(self.num_beacons))
        range_data_at_row = np.array([x[row] for x in beacon_ranges])
        return np.any(~np.isnan(range_data_at_row)).astype(bool)

    def _add_odometry_measurements(self):
        start_pos = self.positions[0]
        start_rot = self.rotations[0]
        curr_pose = self._get_transformation_matrix(start_rot, start_pos)
        prev_pose = self._get_transformation_matrix(start_rot, start_pos)
        base_pose_name = "A0"
        for idx, (position, rot) in enumerate(zip(self.positions, self.rotations)):

            if not self._row_has_range_measures(idx):
                continue

            curr_pose = self._get_transformation_matrix(rot, position)

            # if first pose, then we don't have an odometry measurement
            if idx == 0:
                continue

            relative_rot, relative_trans = get_relative_rot_and_trans_between_poses(
                prev_pose, curr_pose
            )
            to_pose_name = f"A{idx}"
            x, y = relative_trans
            theta = get_theta_from_rotation_matrix(relative_rot)
            # trans_cov = 0.02 ** 2
            # rot_cov = 0.005 ** 2
            trans_cov = 0.02 ** 2
            rot_cov = 0.002 ** 2
            (
                trans_precision,
                rot_precision,
            ) = get_measurement_precisions_from_covariances(trans_cov, rot_cov)
            # trans_precision = 100.
            # rot_precision = 400.
            relative_pose_measurement = PoseMeasurement2D(
                base_pose=base_pose_name,
                to_pose=to_pose_name,
                x=x,
                y=y,
                theta=theta,
                translation_precision=trans_precision,
                rotation_precision=rot_precision,
            )
            self.pyfg.add_odom_measurement(0, relative_pose_measurement)

            base_pose_name = to_pose_name
            prev_pose = curr_pose

    def _add_range_measurements(self):
        range_precision = 10.0
        range_stddev = (1.0 / range_precision) ** 0.5
        range_stddev = 0.75
        for beacon_idx in range(self.num_beacons):
            ranges = self._get_ranges(beacon_idx)
            beacon_name = f"L{beacon_idx}"
            for pose_idx, dist in enumerate(ranges):
                if np.isnan(dist) or np.isinf(dist) or not np.isreal(dist):
                    continue

                if dist < 2.0 or dist > 150.0:
                    pass
                    # logger.info(f"Range {dist} is out of bounds for beacon {beacon_idx}")
                    # continue

                pose_name = f"A{pose_idx}"
                association = (pose_name, beacon_name)
                range_measure = FGRangeMeasurement(association, dist, range_stddev)
                self.pyfg.add_range_measurement(range_measure)

    @property
    def _COLUMN_NAMES(self):
        return {
            "x_pos": "insXYZ_1",
            "y_pos": "insXYZ_2",
            "z_pos": "insXYZ_3",
            "x_pos_gt": "iNav_GT_1",
            "y_pos_gt": "iNav_GT_2",
            "z_pos_gt": "iNav_GT_3",
            "roll": "insRPY_1",
            "pitch": "insRPY_2",
            "yaw": "insRPY_3",
            "x_vel": "insVel_1",
            "y_vel": "insVel_2",
            "z_vel": "insVel_3",
            "range_beacon_1": "ranges_1",
            "range_beacon_2": "ranges_2",
            "range_beacon_3": "ranges_3",
            "range_beacon_4": "ranges_4",
            "range_filtered_1": "filtered_ranges_1",
            "range_filtered_2": "filtered_ranges_2",
            "range_filtered_3": "filtered_ranges_3",
            "range_filtered_4": "filtered_ranges_4",
        }

    @property
    def positions(self) -> np.ndarray:
        position_cols = [self._COLUMN_NAMES[x] for x in ["x_pos", "y_pos", "z_pos"]]
        position_cols = position_cols[: self.dim]  # drop the last column if we are 2D
        positions = self._data[position_cols].to_numpy()
        return positions

    @property
    def gt_positions(self) -> np.ndarray:
        position_cols = [
            self._COLUMN_NAMES[x] for x in ["x_pos_gt", "y_pos_gt", "z_pos_gt"]
        ]
        position_cols = position_cols[: self.dim]
        positions = self._data[position_cols].to_numpy()
        return positions

    @property
    def gt_rotations(self) -> np.ndarray:
        logger.warning(
            "GT rotations are just taken from the corrected"
            " INS data so are same as orientations used to derive odometry"
        )
        return self.rotations

    @property
    def poses(self) -> np.ndarray:
        """If 2D, return poses as [x,y,theta]. If 3D, return poses as
        [x,y,z,roll, pitch, yaw].

        Returns:
            np.array: the poses, each row is a different pose
        """
        positions = self.positions
        rots = self.rotations
        poses = np.concatenate((positions, rots), axis=1)
        return poses

    @property
    def rotations(self) -> np.ndarray:
        if self.dim == 2:
            return self._data[self._COLUMN_NAMES["yaw"]].to_numpy()
        elif self.dim == 3:
            return self._data[
                self._COLUMN_NAMES["roll"],
                self._COLUMN_NAMES["pitch"],
                self._COLUMN_NAMES["yaw"],
            ].to_numpy()
        else:
            raise ValueError(f"dim was {self.dim} but must be 2 or 3")

    @property
    def velocities(self) -> np.ndarray:
        vel_cols = [self._COLUMN_NAMES[x] for x in ["x_vel", "y_vel", "z_vel"]]
        vel_cols = vel_cols[: self.dim]  # drop the last column if we are 2D
        velocities = self._data[vel_cols].to_numpy()
        return velocities

    @property
    def num_beacons(self) -> int:
        return len(self._beacon_locs)

    def _get_ranges(self, beacon_num) -> np.ndarray:
        if self.filter_ranges:
            return self._get_filtered_range(beacon_num)
        else:
            return self._get_unfiltered_range(beacon_num)

    def _get_unfiltered_range(self, beacon_num) -> np.ndarray:
        return self._data[
            self._COLUMN_NAMES["range_beacon_{}".format(beacon_num + 1)]
        ].to_numpy()

    def _get_filtered_range(self, beacon_num) -> np.ndarray:
        return self._data[
            self._COLUMN_NAMES["range_filtered_{}".format(beacon_num + 1)]
        ].to_numpy()


if __name__ == "__main__":

    def get_data_and_beacon_files(data_dir: Path):
        files_in_dir = list(data_dir.glob("*.csv"))
        assert len(files_in_dir) == 2, "There should be two .csv files in the directory"
        beacon_loc_file = [x for x in files_in_dir if "beacon" in x.name.lower()][0]

        # the other file is the data file
        data_file = [x for x in files_in_dir if x != beacon_loc_file][0]
        return data_file, beacon_loc_file

    goats_dirs = [14, 15, 16]
    for dir_num in goats_dirs:
        data_dir = Path(f"~/data/goats/goats_{dir_num}").expanduser()
        data_file, beacon_loc_file = get_data_and_beacon_files(data_dir)

        # load the factor graph from the parser
        dimension = 2
        filter_outlier_ranges = True
        parser = GoatsParser(data_file, beacon_loc_file, dimension, filter_outlier_ranges)  # type: ignore
        pyfg = parser.pyfg

        # save the factor graph as a .pkl file
        pyfg_file_path = str(data_file).replace(".csv", ".pkl")
        pyfg._save_to_pickle_format(pyfg_file_path)
