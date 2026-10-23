"""Filter person detections by a normalized seating polygon."""
from collections import deque
from dataclasses import dataclass
import math
from statistics import median_low


@dataclass(frozen=True)
class Detection:
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
    class_id: int


def validate_polygon(points):
    if not isinstance(points, list) or len(points) < 3:
        raise ValueError("座位区域至少需要三个坐标点")
    polygon = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("区域坐标必须为 [x, y]")
        x, y = point
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in (x, y)):
            raise ValueError("区域坐标必须为0到1之间的有限数值")
        polygon.append((float(x), float(y)))
    if len(set(polygon)) != len(polygon):
        raise ValueError("区域坐标不能重复")
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(polygon, polygon[1:] + polygon[:1]))
    if abs(area) < 1e-9:
        raise ValueError("座位区域面积必须大于零")
    # Convex regions are sufficient for a fixed classroom camera and avoid ambiguous polygons.
    signs = []
    for i, a in enumerate(polygon):
        b, c = polygon[(i + 1) % len(polygon)], polygon[(i + 2) % len(polygon)]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cross) > 1e-9:
            signs.append(cross > 0)
    if not signs or any(s != signs[0] for s in signs):
        raise ValueError("座位区域需为按顺序标注的凸多边形")
    # A convex turn test alone does not rule out a star polygon with five or more vertices.
    for i, a in enumerate(polygon):
        b = polygon[(i + 1) % len(polygon)]
        for j, c in enumerate(polygon):
            if j in (i, (i + 1) % len(polygon)) or (j + 1) % len(polygon) == i:
                continue
            d = polygon[(j + 1) % len(polygon)]
            def side(p, q, r):
                return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
            if side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0:
                raise ValueError("座位区域的边不能相交")
    return polygon


def contains(point, polygon):
    x, y = point
    directions = []
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        cross = (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])
        if abs(cross) > 1e-9:
            directions.append(cross > 0)
    return not directions or all(v == directions[0] for v in directions)


def select_people(detections, width, height, polygon, threshold=0.45, class_ids=(0,)):
    if width <= 0 or height <= 0:
        raise ValueError("画面尺寸必须大于零")
    selected = []
    for box in detections:
        values = (box.x1, box.y1, box.x2, box.y2, box.confidence)
        if not all(math.isfinite(v) for v in values):
            raise ValueError("检测器返回了非有限数值")
        if not 0 <= box.confidence <= 1:
            raise ValueError("检测置信度必须在0到1之间")
        if box.class_id not in class_ids or box.confidence < threshold:
            continue
        if not (0 <= box.x1 < box.x2 <= width and 0 <= box.y1 < box.y2 <= height):
            raise ValueError("检测框超出画面范围或尺寸无效")
        anchor = ((box.x1 + box.x2) / (2 * width), box.y2 / height)
        if contains(anchor, polygon):
            selected.append(box)
    return selected


class CountWindow:
    def __init__(self, size=5):
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 101:
            raise ValueError("计数窗口必须为1到101之间的整数")
        self.values = deque(maxlen=size)

    def add(self, count):
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("人数必须为非负整数")
        self.values.append(count)
        return int(median_low(self.values))

    def clear(self):
        self.values.clear()
