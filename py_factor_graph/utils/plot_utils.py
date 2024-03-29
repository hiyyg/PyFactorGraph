import numpy as np
import math
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.lines as mlines
from typing import Tuple
from py_factor_graph.variables import PoseVariable2D, LandmarkVariable2D
from py_factor_graph.measurements import FGRangeMeasurement
from py_factor_graph.utils.matrix_utils import get_theta_from_rotation_matrix

COLORS = ["blue", "red", "green", "yellow", "black", "cyan", "magenta"]


def get_color(i: int) -> str:
    return COLORS[i % len(COLORS)]


def draw_arrow(
    ax: plt.Axes,
    x: float,
    y: float,
    theta: float,
    quiver_length: float = 0.1,
    quiver_width: float = 0.01,
    color: str = "black",
) -> mpatches.FancyArrow:
    """Draws an arrow on the given axes

    Args:
        ax (plt.Axes): the axes to draw the arrow on
        x (float): the x position of the arrow
        y (float): the y position of the arrow
        theta (float): the angle of the arrow
        quiver_length (float, optional): the length of the arrow. Defaults to 0.1.
        quiver_width (float, optional): the width of the arrow. Defaults to 0.01.
        color (str, optional): color of the arrow. Defaults to "black".

    Returns:
        mpatches.FancyArrow: the arrow
    """
    dx = quiver_length * math.cos(theta)
    dy = quiver_length * math.sin(theta)
    return ax.arrow(
        x,
        y,
        dx,
        dy,
        head_width=quiver_length,
        head_length=quiver_length,
        width=quiver_width,
        color=color,
    )


def draw_line(
    ax: plt.Axes,
    x_start: float,
    y_start: float,
    x_end: float,
    y_end: float,
    color: str = "black",
) -> mlines.Line2D:
    """Draws a line on the given axes between the two points

    Args:
        ax (plt.Axes): the axes to draw the arrow on
        x_start (float): the x position of the start of the line
        y_start (float): the y position of the start of the line
        x_end (float): the x position of the end of the line
        y_end (float): the y position of the end of the line
        color (str, optional): color of the arrow. Defaults to "black".

    Returns:
        mpatches.FancyArrow: the arrow
    """
    line = mlines.Line2D([x_start, x_end], [y_start, y_end], color=color)
    ax.add_line(line)
    return line


def draw_circle(ax: plt.Axes, circle: np.ndarray, color="red") -> mpatches.Circle:
    assert circle.size == 3
    return ax.add_patch(
        mpatches.Circle(circle[0:2], circle[2], color=color, fill=False)
    )


def draw_pose_variable(ax: plt.Axes, pose: PoseVariable2D, color="blue"):
    true_x = pose.true_x
    true_y = pose.true_y
    true_theta = pose.true_theta
    return draw_arrow(ax, true_x, true_y, true_theta, color=color)


def draw_pose_matrix(ax: plt.Axes, pose_matrix: np.ndarray, color="blue"):
    true_x = pose_matrix[0, 2]
    true_y = pose_matrix[1, 2]
    true_theta = get_theta_from_rotation_matrix(pose_matrix[0:2, 0:2])
    return draw_arrow(ax, true_x, true_y, true_theta, color=color)


def draw_landmark_variable(ax: plt.Axes, landmark: LandmarkVariable2D):
    true_x = landmark.true_x
    true_y = landmark.true_y
    ax.scatter(true_x, true_y, color="green", marker=(5, 2))


def draw_loop_closure_measurement(
    ax: plt.Axes, base_loc: np.ndarray, to_pose: PoseVariable2D
) -> Tuple[mlines.Line2D, mpatches.FancyArrow]:
    assert base_loc.size == 2

    x_start = base_loc[0]
    y_start = base_loc[1]
    x_end = to_pose.true_x
    y_end = to_pose.true_y

    line = draw_line(ax, x_start, y_start, x_end, y_end, color="green")
    arrow = draw_pose_variable(ax, to_pose)

    return line, arrow


def draw_range_measurement(
    ax: plt.Axes,
    range_measure: FGRangeMeasurement,
    from_pose: PoseVariable2D,
    to_landmark: LandmarkVariable2D,
) -> Tuple[mlines.Line2D, mpatches.Circle]:
    base_loc = from_pose.true_x, from_pose.true_y
    to_loc = to_landmark.true_x, to_landmark.true_y
    dist = range_measure.dist

    x_start, y_start = base_loc
    x_end, y_end = to_loc

    landmark_idx = int(to_landmark.name[1:])
    c = get_color(landmark_idx)
    line = draw_line(ax, x_start, y_start, x_end, y_end, color=c)
    circle = draw_circle(ax, np.array([x_start, y_start, dist]), color=c)
    # arrow = draw_landmark_variable(ax, to_landmark)

    return line, circle
