from __future__ import annotations

import colorsys
import math
from collections import Counter, deque
from dataclasses import dataclass
from typing import Mapping


RGB = tuple[int, int, int]
Frame = Mapping[str, int | float]

CANONICAL_COMPONENT_KINDS = (
    "skill",
    "agent",
    "hook",
    "rule",
    "command",
    "composite",
)

# The renderer uses one neutral material for every idle node. Canonical kind is
# semantic/AX authority and the central text mark is its visible non-color cue;
# pixels never partition this shared material into synthetic kind buckets.
GRAPH_3D_NEUTRAL_MATERIAL: dict[str, RGB] = {
    "body": (71, 85, 105),
    "outline": (203, 213, 225),
    "mark": (248, 250, 252),
}

GRAPH_SELECTION_HALO_PALETTE: dict[str, RGB] = {
    "halo": (56, 189, 248),
    "underlay": (15, 23, 42),
}


@dataclass
class GraphPixelAnalysisError(ValueError):
    code: str
    detail: str | None = None

    def __str__(self) -> str:
        return self.code if self.detail is None else f"{self.code}: {self.detail}"


def _finite(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GraphPixelAnalysisError(code)
    result = float(value)
    if not math.isfinite(result):
        raise GraphPixelAnalysisError(code)
    return result


def _frame(value: Frame, code: str) -> dict[str, float]:
    try:
        result = {key: _finite(value[key], code) for key in ("x", "y", "width", "height")}
    except (KeyError, TypeError):
        raise GraphPixelAnalysisError(code) from None
    if result["width"] <= 0 or result["height"] <= 0:
        raise GraphPixelAnalysisError(code)
    return result


def _distance(left: RGB, right: RGB) -> float:
    return math.sqrt(sum((left[index] - right[index]) ** 2 for index in range(3)))


def _matches(pixel: RGB, expected: RGB, tolerance: float) -> bool:
    return _distance(pixel, expected) <= tolerance


def _composite(foreground: RGB, background: RGB, opacity: float) -> RGB:
    return tuple(
        round((foreground[index] * opacity) + (background[index] * (1 - opacity)))
        for index in range(3)
    )


def _bbox(points: set[tuple[int, int]] | list[tuple[int, int]], code: str) -> dict[str, int]:
    if not points:
        raise GraphPixelAnalysisError(code)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    return {"x": left, "y": top, "width": right - left + 1, "height": bottom - top + 1}


def _viewport_local_frame(
    pixel_frame: Mapping[str, int],
    *,
    crop_left: int,
    crop_top: int,
    scale: float,
) -> dict[str, float]:
    return {
        "x": (pixel_frame["x"] - crop_left) / scale,
        "y": (pixel_frame["y"] - crop_top) / scale,
        "width": pixel_frame["width"] / scale,
        "height": pixel_frame["height"] / scale,
    }


def _pixel(pixels: bytes, pixel_width: int, x: int, y: int) -> RGB:
    offset = ((y * pixel_width) + x) * 4
    return (pixels[offset], pixels[offset + 1], pixels[offset + 2])


def _estimate_background(
    pixels: bytes,
    pixel_width: int,
    *,
    left: int,
    top: int,
    right: int,
    bottom: int,
) -> RGB:
    area = (right - left) * (bottom - top)
    stride = max(1, math.ceil(math.sqrt(area / 50_000)))
    samples = Counter(
        _pixel(pixels, pixel_width, x, y)
        for y in range(top, bottom, stride)
        for x in range(left, right, stride)
    )
    if not samples:
        raise GraphPixelAnalysisError("graph_viewport_pixels_missing")
    return samples.most_common(1)[0][0]


def _unit_distance(pixel: RGB, target: RGB) -> float:
    pixel_length = math.sqrt(sum(channel * channel for channel in pixel))
    target_length = math.sqrt(sum(channel * channel for channel in target))
    if pixel_length == 0 or target_length == 0:
        return math.inf
    return math.sqrt(
        sum(
            ((pixel[index] / pixel_length) - (target[index] / target_length)) ** 2
            for index in range(3)
        )
    )


def _is_neutral_body(pixel: RGB, background: RGB) -> bool:
    if _distance(pixel, background) < 24:
        return False
    if _matches(pixel, GRAPH_SELECTION_HALO_PALETTE["halo"], 18):
        return False
    _, saturation, value = colorsys.rgb_to_hsv(*(channel / 255 for channel in pixel))
    return (
        0.10 <= saturation <= 0.58
        and 0.10 <= value <= 0.80
        and _unit_distance(pixel, GRAPH_3D_NEUTRAL_MATERIAL["body"]) <= 0.12
    )


def _components(points: set[tuple[int, int]]) -> list[dict[str, object]]:
    remaining = set(points)
    regions: list[dict[str, object]] = []
    while remaining:
        origin = remaining.pop()
        queue = deque([origin])
        region = {origin}
        while queue:
            x, y = queue.popleft()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    candidate = (x + dx, y + dy)
                    if candidate not in remaining:
                        continue
                    remaining.remove(candidate)
                    region.add(candidate)
                    queue.append(candidate)
        frame = _bbox(region, "graph_body_region_missing")
        solidity = len(region) / (frame["width"] * frame["height"])
        if (
            len(region) >= 6
            and min(frame["width"], frame["height"]) >= 2
            and solidity >= 0.12
        ):
            regions.append({"points": region, "frame": frame, "solidity": solidity})
    regions.sort(
        key=lambda region: (
            -len(region["points"]),
            region["frame"]["y"],
            region["frame"]["x"],
        )
    )
    return regions


def _partition_fit_regions(
    regions: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Separate the two visibly larger relation shells from component volume.

    This is a tier/scale claim only. The two shell regions are deliberately not
    assigned to profile or workflow by color, position, or inferred identity.
    """

    if len(regions) < 3:
        raise GraphPixelAnalysisError("graph_relation_shell_pair_missing")
    relation_regions = regions[:2]
    component_regions = regions[2:]
    largest_count = len(relation_regions[0]["points"])
    smaller_shell_count = len(relation_regions[1]["points"])
    largest_component_count = max(
        len(region["points"]) for region in component_regions
    )
    if (
        smaller_shell_count < largest_count * 0.55
        or smaller_shell_count < largest_component_count * 1.5
    ):
        raise GraphPixelAnalysisError("graph_relation_shell_pair_missing")
    return relation_regions, component_regions


def _shared_material_evidence(
    *,
    body_pixel_count: int,
    outline_pixel_count: int,
) -> dict[str, object]:
    return {
        "body_pixel_count": body_pixel_count,
        "outline_pixel_count": outline_pixel_count,
        "body_rgb": list(GRAPH_3D_NEUTRAL_MATERIAL["body"]),
        "outline_rgb": list(GRAPH_3D_NEUTRAL_MATERIAL["outline"]),
    }


def _normalized_expected_counts(value: Mapping[str, int]) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise GraphPixelAnalysisError("graph_expected_kind_counts_invalid")
    normalized: Counter[str] = Counter()
    for raw_kind, raw_count in value.items():
        if (
            not isinstance(raw_kind, str)
            or not raw_kind.strip()
            or isinstance(raw_count, bool)
            or not isinstance(raw_count, int)
            or raw_count < 0
        ):
            raise GraphPixelAnalysisError("graph_expected_kind_counts_invalid")
        if raw_count == 0:
            continue
        kind = raw_kind.strip().lower()
        if kind not in CANONICAL_COMPONENT_KINDS:
            raise GraphPixelAnalysisError(
                "graph_expected_kind_counts_invalid", kind
            )
        normalized[kind] += raw_count
    if not normalized:
        raise GraphPixelAnalysisError("graph_expected_kind_counts_invalid")
    return dict(sorted(normalized.items()))


def _near_regions(
    points: set[tuple[int, int]],
    regions: list[dict[str, object]],
    padding: int,
) -> set[tuple[int, int]]:
    matched: set[tuple[int, int]] = set()
    for x, y in points:
        for region in regions:
            frame = region["frame"]
            if (
                frame["x"] - padding <= x < frame["x"] + frame["width"] + padding
                and frame["y"] - padding <= y < frame["y"] + frame["height"] + padding
            ):
                matched.add((x, y))
                break
    return matched


def _surrounding_sides(
    points: set[tuple[int, int]],
    frame: Mapping[str, int],
) -> dict[str, bool]:
    right = frame["x"] + frame["width"] - 1
    bottom = frame["y"] + frame["height"] - 1
    return {
        "left": any(x < frame["x"] and frame["y"] <= y <= bottom for x, y in points),
        "right": any(x > right and frame["y"] <= y <= bottom for x, y in points),
        "top": any(y < frame["y"] and frame["x"] <= x <= right for x, y in points),
        "bottom": any(y > bottom and frame["x"] <= x <= right for x, y in points),
    }


def _selected_evidence(
    *,
    component_id: str,
    kind: str,
    regions: list[dict[str, object]],
    halo_points: set[tuple[int, int]],
    underlay_points: set[tuple[int, int]],
    pixels: bytes,
    pixel_width: int,
    crop_left: int,
    crop_top: int,
    scale: float,
    appearance: str,
) -> dict[str, object]:
    candidates: list[tuple[int, dict[str, object], set[tuple[int, int]], dict[str, bool]]] = []
    for region in regions:
        frame = region["frame"]
        padding = max(round(5 * scale), round(max(frame["width"], frame["height"]) * 0.4))
        nearby = _near_regions(halo_points, [region], padding)
        sides = _surrounding_sides(nearby, frame)
        candidates.append((sum(sides.values()) * 1_000 + len(nearby), region, nearby, sides))
    if not candidates:
        raise GraphPixelAnalysisError("graph_selected_neutral_body_pixels_missing", kind)
    _, region, nearby_halo, halo_sides = max(candidates, key=lambda candidate: candidate[0])
    if len(nearby_halo) < 8 or not all(halo_sides.values()):
        raise GraphPixelAnalysisError("graph_selection_halo_pixels_missing", component_id)

    frame = region["frame"]
    padding = max(round(7 * scale), round(max(frame["width"], frame["height"]) * 0.5))
    nearby_underlay = _near_regions(underlay_points, [region], padding)
    underlay_sides = _surrounding_sides(nearby_underlay, frame)
    if len(nearby_underlay) < 8 or not all(underlay_sides.values()):
        raise GraphPixelAnalysisError("graph_selection_underlay_pixels_missing", component_id)

    inset_x = max(2, round(frame["width"] * 0.12))
    inset_y = max(2, round(frame["height"] * 0.12))
    mark_left = frame["x"] + inset_x
    mark_top = frame["y"] + inset_y
    mark_right = frame["x"] + frame["width"] - inset_x
    mark_bottom = frame["y"] + frame["height"] - inset_y
    mark_rgb = GRAPH_3D_NEUTRAL_MATERIAL["mark"]
    inside_mark = {
        (x, y)
        for y in range(mark_top, mark_bottom)
        for x in range(mark_left, mark_right)
        if _matches(_pixel(pixels, pixel_width, x, y), mark_rgb, 12)
    }
    if len(inside_mark) < 4:
        raise GraphPixelAnalysisError("graph_kind_mark_pixels_missing", kind)
    mark_frame = _bbox(inside_mark, "graph_kind_mark_pixels_missing")
    body_center = (
        frame["x"] + ((frame["width"] - 1) / 2),
        frame["y"] + ((frame["height"] - 1) / 2),
    )
    mark_center = (
        mark_frame["x"] + ((mark_frame["width"] - 1) / 2),
        mark_frame["y"] + ((mark_frame["height"] - 1) / 2),
    )
    center_delta = {
        "x": abs(mark_center[0] - body_center[0]) / scale,
        "y": abs(mark_center[1] - body_center[1]) / scale,
    }
    tolerance = max(2.0, (min(frame["width"], frame["height"]) / scale) * 0.10)
    if center_delta["x"] > tolerance or center_delta["y"] > tolerance:
        raise GraphPixelAnalysisError("graph_kind_mark_off_center", kind)

    return {
        "component_id": component_id,
        "kind_token": kind,
        "body_frame": _viewport_local_frame(
            frame, crop_left=crop_left, crop_top=crop_top, scale=scale
        ),
        "mark_frame": _viewport_local_frame(
            mark_frame, crop_left=crop_left, crop_top=crop_top, scale=scale
        ),
        "mark_detected": True,
        "mark_center_delta_css_px": center_delta,
        "mark_center_tolerance_css_px": tolerance,
        "selection_halo_detected": True,
        "selection_halo_pixel_count": len(nearby_halo),
        "selection_halo_sides": dict(sorted(halo_sides.items())),
        "selection_underlay_detected": True,
        "selection_underlay_pixel_count": len(nearby_underlay),
        "selection_underlay_sides": dict(sorted(underlay_sides.items())),
        "appearance": appearance,
    }


def analyze_graph_viewport_pixels(
    *,
    pixels: bytes,
    pixel_width: int,
    pixel_height: int,
    point_pixel_scale: int | float,
    capture_content_frame: Frame,
    viewport_frame: Frame,
    appearance: str,
    zoom_state: str,
    expected_kind_counts: Mapping[str, int],
    selected_component_id: str | None = None,
    selected_kind_token: str | None = None,
) -> dict[str, object]:
    """Prove 3D graph pixels using one semantic viewport crop.

    Semantic/AX data supplies only viewport bounds, expected identities and the
    selected identity. No per-node accessibility frame participates in pixel
    classification or selected-node localization.
    """

    if (
        not isinstance(pixel_width, int)
        or isinstance(pixel_width, bool)
        or not isinstance(pixel_height, int)
        or isinstance(pixel_height, bool)
        or pixel_width <= 0
        or pixel_height <= 0
        or not isinstance(pixels, bytes)
        or len(pixels) != pixel_width * pixel_height * 4
    ):
        raise GraphPixelAnalysisError("graph_pixel_input_invalid")
    if appearance not in {"light", "dark"}:
        raise GraphPixelAnalysisError("graph_appearance_invalid")
    if zoom_state not in {"fit", "direct", "maximum"}:
        raise GraphPixelAnalysisError("graph_zoom_state_invalid")

    scale = _finite(point_pixel_scale, "graph_pixel_scale_invalid")
    if scale <= 0:
        raise GraphPixelAnalysisError("graph_pixel_scale_invalid")
    capture = _frame(capture_content_frame, "graph_capture_content_frame_invalid")
    viewport = _frame(viewport_frame, "graph_viewport_frame_invalid")
    if (
        abs(pixel_width - capture["width"] * scale) > 1
        or abs(pixel_height - capture["height"] * scale) > 1
    ):
        raise GraphPixelAnalysisError("graph_screenshot_geometry_mismatch")
    tolerance = 1 / scale
    if (
        viewport["x"] < capture["x"] - tolerance
        or viewport["y"] < capture["y"] - tolerance
        or viewport["x"] + viewport["width"] > capture["x"] + capture["width"] + tolerance
        or viewport["y"] + viewport["height"] > capture["y"] + capture["height"] + tolerance
    ):
        raise GraphPixelAnalysisError("graph_viewport_outside_capture")

    left = max(0, math.floor((viewport["x"] - capture["x"]) * scale))
    top = max(0, math.floor((viewport["y"] - capture["y"]) * scale))
    right = min(pixel_width, math.ceil((viewport["x"] + viewport["width"] - capture["x"]) * scale))
    bottom = min(
        pixel_height,
        math.ceil((viewport["y"] + viewport["height"] - capture["y"]) * scale),
    )
    if right - left < 32 or bottom - top < 32:
        raise GraphPixelAnalysisError("graph_viewport_too_small")

    normalized_counts = _normalized_expected_counts(expected_kind_counts)
    if zoom_state == "fit":
        if selected_component_id is not None or selected_kind_token is not None:
            raise GraphPixelAnalysisError("graph_selection_input_invalid")
    elif (
        not isinstance(selected_component_id, str)
        or not selected_component_id.startswith("harnesskit.")
        or not isinstance(selected_kind_token, str)
        or not selected_kind_token.strip()
    ):
        raise GraphPixelAnalysisError("graph_selection_input_invalid")

    selected_kind = None
    if selected_kind_token is not None:
        selected_kind = selected_kind_token.strip().lower()
        if (
            selected_kind not in CANONICAL_COMPONENT_KINDS
            or selected_kind not in normalized_counts
        ):
            raise GraphPixelAnalysisError(
                "graph_selected_kind_not_in_semantic_counts", selected_kind
            )
    background = _estimate_background(
        pixels,
        pixel_width,
        left=left,
        top=top,
        right=right,
        bottom=bottom,
    )
    background_luminance = (
        (0.2126 * background[0]) + (0.7152 * background[1]) + (0.0722 * background[2])
    ) / 255
    if appearance == "light" and background_luminance < 0.55:
        raise GraphPixelAnalysisError("graph_light_background_not_observed")
    if appearance == "dark" and background_luminance > 0.50:
        raise GraphPixelAnalysisError("graph_dark_background_not_observed")

    neutral_body_points: set[tuple[int, int]] = set()
    neutral_outline_points: set[tuple[int, int]] = set()
    halo_points: set[tuple[int, int]] = set()
    underlay_points: set[tuple[int, int]] = set()
    classification_cache: dict[RGB, tuple[bool, bool, bool, bool]] = {}
    outline_target = _composite(
        GRAPH_3D_NEUTRAL_MATERIAL["outline"], background, 0.72
    )

    for y in range(top, bottom):
        for x in range(left, right):
            rgb = _pixel(pixels, pixel_width, x, y)
            classified = classification_cache.get(rgb)
            if classified is None:
                is_halo = zoom_state != "fit" and _matches(
                    rgb, GRAPH_SELECTION_HALO_PALETTE["halo"], 12
                )
                is_underlay = zoom_state != "fit" and _matches(
                    rgb, GRAPH_SELECTION_HALO_PALETTE["underlay"], 7
                )
                is_neutral_body = not is_halo and _is_neutral_body(rgb, background)
                is_neutral_outline = (
                    _distance(rgb, background) >= 12
                    and _distance(rgb, outline_target) <= 34
                )
                classified = (
                    is_halo,
                    is_underlay,
                    is_neutral_body,
                    is_neutral_outline,
                )
                classification_cache[rgb] = classified
            is_halo, is_underlay, is_neutral_body, is_neutral_outline = classified
            if is_halo:
                halo_points.add((x, y))
                continue
            if is_underlay:
                underlay_points.add((x, y))
            if is_neutral_body:
                neutral_body_points.add((x, y))
            if is_neutral_outline:
                neutral_outline_points.add((x, y))

    all_regions = _components(neutral_body_points)
    if not all_regions:
        raise GraphPixelAnalysisError("graph_node_body_pixels_missing")
    relation_regions: list[dict[str, object]] = []
    component_regions = all_regions
    if zoom_state == "fit":
        relation_regions, component_regions = _partition_fit_regions(all_regions)
    if not component_regions:
        raise GraphPixelAnalysisError("graph_neutral_component_body_pixels_missing")

    outline_padding = max(2, math.ceil(2 * scale))
    shared_component_body_count = sum(
        len(region["points"]) for region in component_regions
    )
    shared_component_outline = _near_regions(
        neutral_outline_points, component_regions, outline_padding
    )
    if len(shared_component_outline) < 4:
        raise GraphPixelAnalysisError("graph_neutral_component_outline_pixels_missing")
    shared_neutral_material_evidence = _shared_material_evidence(
        body_pixel_count=shared_component_body_count,
        outline_pixel_count=len(shared_component_outline),
    )

    relation_tier_evidence = None
    if zoom_state == "fit":
        shared_relation_body_count = sum(
            len(region["points"]) for region in relation_regions
        )
        shared_relation_outline = _near_regions(
            neutral_outline_points, relation_regions, outline_padding
        )
        if len(shared_relation_outline) < 4:
            raise GraphPixelAnalysisError("graph_relation_shell_outline_pixels_missing")
        relation_tier_evidence = {
            "tier": "featured_relation",
            "region_count": len(relation_regions),
            **_shared_material_evidence(
                body_pixel_count=shared_relation_body_count,
                outline_pixel_count=len(shared_relation_outline),
            ),
        }

    central_component_volume_evidence = None
    if zoom_state == "fit":
        central_left = left + round((right - left) * 0.25)
        central_right = right - round((right - left) * 0.25)
        central_points = {
            (x, y)
            for region in component_regions
            for x, y in region["points"]
            if central_left <= x < central_right
        }
        if len(central_points) < 6:
            raise GraphPixelAnalysisError("graph_central_component_volume_missing")
        central_frame = _bbox(
            central_points, "graph_central_component_volume_missing"
        )
        central_component_volume_evidence = {
            "present": True,
            "body_pixel_count": len(central_points),
            "frame": _viewport_local_frame(
                central_frame, crop_left=left, crop_top=top, scale=scale
            ),
            "central_band_frame": {
                "x": (central_left - left) / scale,
                "y": 0.0,
                "width": (central_right - central_left) / scale,
                "height": (bottom - top) / scale,
            },
        }

    extent_points = {
        point
        for region in all_regions
        for point in region["points"]
    }
    extent = _bbox(extent_points, "graph_node_body_pixels_missing")
    horizontal_occupancy = extent["width"] / (right - left)
    vertical_occupancy = extent["height"] / (bottom - top)
    largest_dimension_occupancy = max(
        horizontal_occupancy,
        vertical_occupancy,
    )
    node_crop_detected = (
        extent["x"] <= left
        or extent["y"] <= top
        or extent["x"] + extent["width"] >= right
        or extent["y"] + extent["height"] >= bottom
    )
    if node_crop_detected:
        raise GraphPixelAnalysisError("graph_node_crop_detected")
    if zoom_state == "fit" and largest_dimension_occupancy < 0.75:
        raise GraphPixelAnalysisError(
            "graph_fit_largest_dimension_occupancy_insufficient",
            f"{largest_dimension_occupancy:.4f}",
        )

    selection = None
    if zoom_state != "fit":
        assert selected_component_id is not None and selected_kind is not None
        selection = _selected_evidence(
            component_id=selected_component_id,
            kind=selected_kind,
            regions=component_regions,
            halo_points=halo_points,
            underlay_points=underlay_points,
            pixels=pixels,
            pixel_width=pixel_width,
            crop_left=left,
            crop_top=top,
            scale=scale,
            appearance=appearance,
        )

    return {
        "proof_scope": "viewport_pixels",
        "node_frame_authority": "none",
        "exact_identity_count_authority": "typed_projection_semantic_identity_set",
        "pixel_claim_scope": (
            "shared_neutral_material_presence_crop_utilization_relation_tier_central_volume"
            if zoom_state == "fit"
            else "shared_neutral_material_selected_mark_selection_halo"
        ),
        "appearance": appearance,
        "zoom_state": zoom_state,
        "viewport_frame": dict(viewport),
        "viewport_pixel_frame": {
            "x": left,
            "y": top,
            "width": right - left,
            "height": bottom - top,
        },
        "estimated_background_rgb": list(background),
        "expected_kind_counts": normalized_counts,
        "shared_neutral_material_evidence": shared_neutral_material_evidence,
        "relation_tier_evidence": relation_tier_evidence,
        "central_component_volume_evidence": central_component_volume_evidence,
        "node_extent_frame": _viewport_local_frame(
            extent, crop_left=left, crop_top=top, scale=scale
        ),
        "largest_dimension_occupancy": largest_dimension_occupancy,
        "node_crop_detected": node_crop_detected,
        "selected_evidence": selection,
    }
