from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from scripts.package.graph_readability_pixels import (
    GRAPH_3D_NEUTRAL_MATERIAL,
    GRAPH_SELECTION_HALO_PALETTE,
    GraphPixelAnalysisError,
    analyze_graph_viewport_pixels,
)


ROOT = Path(__file__).resolve().parents[2]


def _rgb_hex(rgb: tuple[int, int, int]) -> str:
    return "0x" + "".join(f"{channel:02x}" for channel in rgb)


def _css_hex(rgb: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{channel:02x}" for channel in rgb)


def _composite(
    foreground: tuple[int, int, int],
    background: tuple[int, int, int],
    opacity: float,
) -> tuple[int, int, int]:
    return tuple(
        round((foreground[index] * opacity) + (background[index] * (1 - opacity)))
        for index in range(3)
    )


def _canvas(
    appearance: str,
    *,
    width: int = 300,
    height: int = 180,
) -> tuple[int, int, tuple[int, int, int], bytearray]:
    background = (248, 250, 252) if appearance == "light" else (15, 15, 29)
    pixels = bytearray(bytes((*background, 255)) * width * height)
    return width, height, background, pixels


def _paint(
    pixels: bytearray,
    width: int,
    x: int,
    y: int,
    rgb: tuple[int, int, int],
) -> None:
    offset = ((y * width) + x) * 4
    pixels[offset : offset + 4] = bytes((*rgb, 255))


def _paint_node(
    *,
    pixels: bytearray,
    width: int,
    background: tuple[int, int, int],
    kind: str,
    x: int,
    y: int,
    node_width: int = 28,
    node_height: int = 20,
    selected: bool = False,
    with_selection_underlay: bool = True,
    with_mark: bool = False,
    mark_offset: tuple[int, int] = (0, 0),
    body_light_scale: float = 1.0,
) -> None:
    del kind
    palette = GRAPH_3D_NEUTRAL_MATERIAL
    body = tuple(round(channel * body_light_scale) for channel in palette["body"])
    if selected:
        underlay = GRAPH_SELECTION_HALO_PALETTE["underlay"]
        halo = GRAPH_SELECTION_HALO_PALETTE["halo"]
        underlay_x = x - max(3, round(node_width * 0.17))
        underlay_y = y - max(3, round(node_height * 0.17))
        underlay_width = node_width + (2 * (x - underlay_x))
        underlay_height = node_height + (2 * (y - underlay_y))
        halo_x = x - max(2, round(node_width * 0.10))
        halo_y = y - max(2, round(node_height * 0.10))
        halo_width = node_width + (2 * (x - halo_x))
        halo_height = node_height + (2 * (y - halo_y))
        if with_selection_underlay:
            for xx in range(underlay_x, underlay_x + underlay_width):
                _paint(pixels, width, xx, underlay_y, underlay)
                _paint(pixels, width, xx, underlay_y + underlay_height - 1, underlay)
            for yy in range(underlay_y, underlay_y + underlay_height):
                _paint(pixels, width, underlay_x, yy, underlay)
                _paint(pixels, width, underlay_x + underlay_width - 1, yy, underlay)
        for xx in range(halo_x, halo_x + halo_width):
            _paint(pixels, width, xx, halo_y, halo)
            _paint(pixels, width, xx, halo_y + halo_height - 1, halo)
        for yy in range(halo_y, halo_y + halo_height):
            _paint(pixels, width, halo_x, yy, halo)
            _paint(pixels, width, halo_x + halo_width - 1, yy, halo)
    outline = _composite(palette["outline"], background, 0.72)
    for yy in range(y + 1, y + node_height - 1):
        for xx in range(x + 1, x + node_width - 1):
            _paint(pixels, width, xx, yy, body)
    for xx in range(x, x + node_width):
        _paint(pixels, width, xx, y, outline)
        _paint(pixels, width, xx, y + node_height - 1, outline)
    for yy in range(y, y + node_height):
        _paint(pixels, width, x, yy, outline)
        _paint(pixels, width, x + node_width - 1, yy, outline)
    if with_mark:
        center_x = x + (node_width // 2) + mark_offset[0]
        center_y = y + (node_height // 2) + mark_offset[1]
        for xx, yy in (
            (center_x - 1, center_y - 1),
            (center_x, center_y - 1),
            (center_x + 1, center_y - 1),
            (center_x - 1, center_y),
            (center_x, center_y),
            (center_x + 1, center_y),
            (center_x, center_y + 1),
        ):
            _paint(pixels, width, xx, yy, palette["mark"])


def _paint_fit_relation_shells(
    *,
    pixels: bytearray,
    width: int,
    background: tuple[int, int, int],
    profile_x: int = 24,
    workflow_x: int = 228,
    profile_y: int = 26,
    workflow_y: int = 114,
) -> None:
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="profile",
        x=profile_x,
        y=profile_y,
        node_width=48,
        node_height=30,
    )
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="workflow",
        x=workflow_x,
        y=workflow_y,
        node_width=48,
        node_height=30,
    )


def _analyze(
    *,
    pixels: bytes,
    width: int,
    height: int,
    appearance: str,
    zoom_state: str,
    expected_kind_counts: dict[str, int],
    selected_component_id: str | None = None,
    selected_kind_token: str | None = None,
    point_pixel_scale: float = 1,
) -> dict[str, object]:
    return analyze_graph_viewport_pixels(
        pixels=pixels,
        pixel_width=width,
        pixel_height=height,
        point_pixel_scale=point_pixel_scale,
        capture_content_frame={
            "x": 500,
            "y": 200,
            "width": width / point_pixel_scale,
            "height": height / point_pixel_scale,
        },
        viewport_frame={"x": 520, "y": 220, "width": 260, "height": 140},
        appearance=appearance,
        zoom_state=zoom_state,
        expected_kind_counts=expected_kind_counts,
        selected_component_id=selected_component_id,
        selected_kind_token=selected_kind_token,
    )


def test_runtime_node_material_matches_sot81_appearance_and_envelope_authority() -> None:
    source = (ROOT / "src-frontend" / "graph" / "node-object.js").read_text(
        encoding="utf-8"
    )
    metrics_source = (
        ROOT / "src-frontend" / "graph" / "visual-metrics.js"
    ).read_text(encoding="utf-8")
    spatial_source = (
        ROOT / "src-frontend" / "graph" / "spatial-policy.js"
    ).read_text(encoding="utf-8")
    component_map_source = (
        ROOT / "src-frontend" / "graph" / "component-map.js"
    ).read_text(encoding="utf-8")

    # SOT-81 has per-appearance neutral materials, not the retired single
    # NEUTRAL_PALETTE and cyan double-halo selection treatment.
    palette_pattern = (
        r"const APPEARANCE_PALETTES = Object\.freeze\(\{\s*"
        r"dark: Object\.freeze\(\{\s*"
        r"body: (?P<dark_body>0x[0-9a-f]+),\s*"
        r"outline: (?P<dark_outline>0x[0-9a-f]+),\s*"
        r"rim: (?P<dark_rim>0x[0-9a-f]+),\s*"
        r'text: "(?P<dark_text>#[0-9a-f]+)",?\s*\}\),\s*'
        r"light: Object\.freeze\(\{\s*"
        r"body: (?P<light_body>0x[0-9a-f]+),\s*"
        r"outline: (?P<light_outline>0x[0-9a-f]+),\s*"
        r"rim: (?P<light_rim>0x[0-9a-f]+),\s*"
        r'text: "(?P<light_text>#[0-9a-f]+)",?\s*\}\),?\s*\}\);'
    )
    palette = re.search(palette_pattern, source)
    assert palette is not None
    assert palette.groupdict() == {
        "dark_body": "0x475569",
        "dark_outline": "0xcbd5e1",
        "dark_rim": "0xf8fafc",
        "dark_text": "#f8fafc",
        "light_body": "0x64748b",
        "light_outline": "0x334155",
        "light_rim": "0x0f172a",
        "light_text": "#0f172a",
    }
    assert "emissive: 0x000000" in source
    assert "emissiveIntensity: 0" in source
    assert "NEUTRAL_PALETTE" not in source
    assert "selection-halo" not in source
    assert "0x38bdf8" not in source

    # One primary focus owns one rim; selection is an outline state only.
    assert source.count('harnesskitRole = "focus-rim"') == 1
    assert "const primaryFocus = focusNodeId === id;" in source
    assert "record.rim.visible = normalized > 0.5;" in source
    assert "record.rim.visible = false;" in source
    assert "targetScale: primaryFocus ? geometryPolicy.focusPresentationScale : 1" in source
    assert "emphasis === \"selected\" ? \"idle\" : emphasis" in source

    # Visual presentation can grow for focus, while hit, collision, and Fit
    # retain their separately derived authority.
    assert "componentBodyRadius: 3" in spatial_source
    assert "relationBodyRadius: 7.5" in spatial_source
    assert "relationMaximumScale: 1.15" in spatial_source
    assert "outlineShellScale: 1.08" in spatial_source
    assert "focusRimScale: 1.16" in spatial_source
    assert "focusPresentationScale: 1.06" in spatial_source
    assert "hitTargetScale: 1.35" in spatial_source
    assert "collisionEnvelopeScale: 1.34" in spatial_source
    assert "componentRadius: 4" in spatial_source
    assert "relationRadius: 14" in spatial_source
    assert "focusRimScale\n    * policy.visualGeometryScale.focusPresentationScale" in metrics_source
    assert "* policy.layout.collisionEnvelopeScale" in metrics_source
    assert "export function graphNodeVisualEnvelope" in metrics_source
    assert "export function graphNodeLayoutEnvelope" in metrics_source
    assert "graphNodeVisualEnvelope" in component_map_source
    assert "graphNodeLayoutEnvelope" in component_map_source
    assert "hitTarget.scale.setScalar(geometryPolicy.hitTargetScale)" in source
    assert re.search(
        r"interpolated\(\s*collapsedRimScale,\s*"
        r"geometryPolicy\.focusRimScale,\s*secondPhaseProgress,?\s*\)",
        source,
    )
    assert "colorWrite: false" in source


@pytest.mark.parametrize("appearance", ("light", "dark"))
def test_fit_reports_one_shared_neutral_material_pool(
    appearance: str,
) -> None:
    width, height, background, pixels = _canvas(appearance)
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="skill",
        x=24,
        y=62,
    )
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="agent",
        x=132,
        y=70,
    )
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="hook",
        x=246,
        y=58,
    )
    _paint_fit_relation_shells(
        pixels=pixels,
        width=width,
        background=background,
    )
    # Same palette outside the semantic viewport must not count as graph proof.
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="rule",
        x=0,
        y=0,
        node_width=12,
        node_height=12,
    )

    observed = _analyze(
        pixels=bytes(pixels),
        width=width,
        height=height,
        appearance=appearance,
        zoom_state="fit",
        expected_kind_counts={"skill": 55, "agent": 26, "hook": 1},
    )

    assert observed["proof_scope"] == "viewport_pixels"
    assert observed["node_frame_authority"] == "none"
    assert observed["appearance"] == appearance
    assert observed["zoom_state"] == "fit"
    assert observed["expected_kind_counts"] == {"agent": 26, "hook": 1, "skill": 55}
    assert "kind_evidence" not in observed
    evidence = observed["shared_neutral_material_evidence"]
    assert evidence["body_pixel_count"] > 100
    assert evidence["outline_pixel_count"] >= 20
    assert evidence["body_rgb"] == list(GRAPH_3D_NEUTRAL_MATERIAL["body"])
    assert evidence["outline_rgb"] == list(GRAPH_3D_NEUTRAL_MATERIAL["outline"])
    assert observed["largest_dimension_occupancy"] >= 0.75
    assert observed["node_crop_detected"] is False
    assert observed["selected_evidence"] is None


@pytest.mark.parametrize("appearance", ("light", "dark"))
def test_fit_proves_relation_shells_and_central_component_volume(
    appearance: str,
) -> None:
    width, height, background, pixels = _canvas(appearance)
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="profile",
        x=24,
        y=26,
        node_width=48,
        node_height=30,
    )
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="skill",
        x=24,
        y=70,
    )
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="agent",
        x=132,
        y=70,
    )
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="hook",
        x=246,
        y=70,
    )
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="workflow",
        x=228,
        y=114,
        node_width=48,
        node_height=30,
    )

    observed = _analyze(
        pixels=bytes(pixels),
        width=width,
        height=height,
        appearance=appearance,
        zoom_state="fit",
        expected_kind_counts={"skill": 55, "agent": 26, "hook": 1},
    )

    assert "relation_shell_evidence" not in observed
    relation_tier = observed["relation_tier_evidence"]
    assert relation_tier["tier"] == "featured_relation"
    assert relation_tier["region_count"] == 2
    assert relation_tier["body_pixel_count"] > 100
    assert relation_tier["outline_pixel_count"] >= 20
    assert relation_tier["body_rgb"] == list(GRAPH_3D_NEUTRAL_MATERIAL["body"])
    assert relation_tier["outline_rgb"] == list(
        GRAPH_3D_NEUTRAL_MATERIAL["outline"]
    )
    central = observed["central_component_volume_evidence"]
    assert central["body_pixel_count"] > 100
    assert central["present"] is True
    assert central["frame"]["width"] > 0
    assert central["central_band_frame"] == {
        "x": 65.0,
        "y": 0.0,
        "width": 130.0,
        "height": 140.0,
    }


def test_fit_fails_closed_without_a_profile_relation_shell() -> None:
    width, height, background, pixels = _canvas("light")
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="skill",
        x=132,
        y=70,
    )
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="workflow",
        x=228,
        y=114,
        node_width=48,
        node_height=30,
    )

    with pytest.raises(GraphPixelAnalysisError) as captured:
        _analyze(
            pixels=bytes(pixels),
            width=width,
            height=height,
            appearance="light",
            zoom_state="fit",
            expected_kind_counts={"skill": 1},
        )

    assert captured.value.code == "graph_relation_shell_pair_missing"
    assert captured.value.detail is None


def test_fit_fails_closed_without_central_component_volume() -> None:
    width, height, background, pixels = _canvas("light")
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="skill",
        x=24,
        y=70,
    )
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="agent",
        x=246,
        y=70,
    )
    _paint_fit_relation_shells(
        pixels=pixels,
        width=width,
        background=background,
    )

    with pytest.raises(GraphPixelAnalysisError) as captured:
        _analyze(
            pixels=bytes(pixels),
            width=width,
            height=height,
            appearance="light",
            zoom_state="fit",
            expected_kind_counts={"skill": 1, "agent": 1},
        )

    assert captured.value.code == "graph_central_component_volume_missing"


@pytest.mark.parametrize("appearance", ("light", "dark"))
def test_direct_zoom_discovers_selected_node_without_an_ax_node_frame(
    appearance: str,
) -> None:
    width, height, background, pixels = _canvas(appearance)
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="skill",
        x=26,
        y=64,
    )
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="skill",
        x=142,
        y=58,
        node_width=54,
        node_height=38,
        selected=True,
        with_mark=True,
    )
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="agent",
        x=246,
        y=62,
    )

    observed = _analyze(
        pixels=bytes(pixels),
        width=width,
        height=height,
        appearance=appearance,
        zoom_state="direct",
        expected_kind_counts={"skill": 55, "agent": 26},
        selected_component_id="harnesskit.skill.tdd",
        selected_kind_token="skill",
    )

    selected = observed["selected_evidence"]
    assert observed["pixel_claim_scope"] == (
        "shared_neutral_material_selected_mark_selection_halo"
    )
    assert observed["relation_tier_evidence"] is None
    assert observed["central_component_volume_evidence"] is None
    assert selected["component_id"] == "harnesskit.skill.tdd"
    assert selected["kind_token"] == "skill"
    assert selected["selection_halo_detected"] is True
    assert selected["selection_halo_pixel_count"] >= 20
    assert selected["selection_halo_sides"] == {
        "bottom": True,
        "left": True,
        "right": True,
        "top": True,
    }
    assert selected["selection_underlay_detected"] is True
    assert selected["mark_detected"] is True
    assert selected["mark_center_delta_css_px"]["x"] <= 2
    assert selected["mark_center_delta_css_px"]["y"] <= 2
    assert selected["body_frame"]["width"] >= 40
    assert "node_frame" not in inspect.signature(analyze_graph_viewport_pixels).parameters


def test_retina_capture_preserves_css_geometry_for_selected_pixel_evidence() -> None:
    width, height, background, pixels = _canvas("dark", width=600, height=360)
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="agent",
        x=264,
        y=116,
        node_width=108,
        node_height=76,
        selected=True,
        with_mark=True,
    )

    observed = _analyze(
        pixels=bytes(pixels),
        width=width,
        height=height,
        appearance="dark",
        zoom_state="direct",
        expected_kind_counts={"agent": 26},
        selected_component_id="harnesskit.agent.example",
        selected_kind_token="agent",
        point_pixel_scale=2,
    )

    assert observed["viewport_pixel_frame"] == {
        "x": 40,
        "y": 40,
        "width": 520,
        "height": 280,
    }
    assert observed["selected_evidence"]["body_frame"]["width"] >= 50
    assert observed["selected_evidence"]["mark_center_delta_css_px"]["x"] <= 2


def test_pixel_proof_rejects_noncanonical_synthetic_kind_counts() -> None:
    width, height, background, pixels = _canvas("dark")
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="unknown",
        x=24,
        y=60,
    )
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="profile",
        x=244,
        y=60,
    )

    with pytest.raises(GraphPixelAnalysisError) as captured:
        _analyze(
            pixels=bytes(pixels),
            width=width,
            height=height,
            appearance="dark",
            zoom_state="fit",
            expected_kind_counts={"future-kind": 1},
        )

    assert captured.value.code == "graph_expected_kind_counts_invalid"
    assert captured.value.detail == "future-kind"


def test_fit_accepts_lit_neutral_body_shades_without_kind_color_classification() -> None:
    width, height, background, pixels = _canvas("dark")
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="skill",
        x=132,
        y=60,
        body_light_scale=0.55,
    )
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="command",
        x=246,
        y=60,
        body_light_scale=0.72,
    )
    _paint_fit_relation_shells(
        pixels=pixels,
        width=width,
        background=background,
    )

    observed = _analyze(
        pixels=bytes(pixels),
        width=width,
        height=height,
        appearance="dark",
        zoom_state="fit",
        expected_kind_counts={"skill": 1, "command": 1},
    )

    assert "kind_evidence" not in observed
    assert observed["shared_neutral_material_evidence"]["body_pixel_count"] > 0


def test_fit_does_not_falsely_partition_neutral_pixels_by_semantic_kind() -> None:
    width, height, background, pixels = _canvas("light")
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="skill",
        x=132,
        y=60,
    )
    _paint_fit_relation_shells(
        pixels=pixels,
        width=width,
        background=background,
    )

    observed = _analyze(
        pixels=bytes(pixels),
        width=width,
        height=height,
        appearance="light",
        zoom_state="fit",
        expected_kind_counts={"skill": 1, "agent": 1},
    )

    assert "kind_evidence" not in observed
    assert observed["expected_kind_counts"] == {"agent": 1, "skill": 1}
    assert observed["shared_neutral_material_evidence"]["body_pixel_count"] > 0


def test_fit_uses_typed_projection_for_counts_instead_of_pixel_region_equality() -> None:
    width, height, background, pixels = _canvas("light")
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="skill",
        x=24,
        y=60,
    )
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="agent",
        x=132,
        y=60,
    )
    _paint_fit_relation_shells(
        pixels=pixels,
        width=width,
        background=background,
    )

    observed = _analyze(
        pixels=bytes(pixels),
        width=width,
        height=height,
        appearance="light",
        zoom_state="fit",
        expected_kind_counts={"skill": 30, "agent": 12},
    )

    assert observed["exact_identity_count_authority"] == (
        "typed_projection_semantic_identity_set"
    )
    assert observed["pixel_claim_scope"] == (
        "shared_neutral_material_presence_crop_utilization_relation_tier_central_volume"
    )
    assert observed["expected_kind_counts"] == {"agent": 12, "skill": 30}
    assert "kind_evidence" not in observed
    assert "semantic_component_count" not in observed[
        "shared_neutral_material_evidence"
    ]


def test_direct_zoom_fails_closed_without_a_selection_halo() -> None:
    width, height, background, pixels = _canvas("light")
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="skill",
        x=132,
        y=58,
        node_width=54,
        node_height=38,
        selected=False,
        with_mark=True,
    )

    with pytest.raises(GraphPixelAnalysisError) as captured:
        _analyze(
            pixels=bytes(pixels),
            width=width,
            height=height,
            appearance="light",
            zoom_state="direct",
            expected_kind_counts={"skill": 1},
            selected_component_id="harnesskit.skill.tdd",
            selected_kind_token="skill",
        )

    assert captured.value.code == "graph_selection_halo_pixels_missing"


def test_direct_zoom_does_not_claim_faded_unrelated_kind_pixels() -> None:
    width, height, background, pixels = _canvas("dark")
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="skill",
        x=132,
        y=58,
        node_width=54,
        node_height=38,
        selected=True,
        with_mark=True,
    )

    observed = _analyze(
        pixels=bytes(pixels),
        width=width,
        height=height,
        appearance="dark",
        zoom_state="direct",
        expected_kind_counts={"skill": 55, "agent": 26},
        selected_component_id="harnesskit.skill.tdd",
        selected_kind_token="skill",
    )

    assert observed["expected_kind_counts"] == {"agent": 26, "skill": 55}
    assert "kind_evidence" not in observed
    assert observed["shared_neutral_material_evidence"]["body_pixel_count"] > 0


def test_direct_zoom_fails_closed_without_selection_contrast_underlay() -> None:
    width, height, background, pixels = _canvas("light")
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="skill",
        x=132,
        y=58,
        node_width=54,
        node_height=38,
        selected=True,
        with_selection_underlay=False,
        with_mark=True,
    )

    with pytest.raises(GraphPixelAnalysisError) as captured:
        _analyze(
            pixels=bytes(pixels),
            width=width,
            height=height,
            appearance="light",
            zoom_state="direct",
            expected_kind_counts={"skill": 1},
            selected_component_id="harnesskit.skill.tdd",
            selected_kind_token="skill",
        )

    assert captured.value.code == "graph_selection_underlay_pixels_missing"


def test_direct_zoom_fails_closed_when_kind_mark_is_off_center() -> None:
    width, height, background, pixels = _canvas("light")
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="skill",
        x=132,
        y=58,
        node_width=54,
        node_height=38,
        selected=True,
        with_mark=True,
        mark_offset=(16, 0),
    )

    with pytest.raises(GraphPixelAnalysisError) as captured:
        _analyze(
            pixels=bytes(pixels),
            width=width,
            height=height,
            appearance="light",
            zoom_state="maximum",
            expected_kind_counts={"skill": 1},
            selected_component_id="harnesskit.skill.tdd",
            selected_kind_token="skill",
        )

    assert captured.value.code == "graph_kind_mark_off_center"


def test_analyzer_rejects_capture_and_viewport_geometry_drift() -> None:
    width, height, _, pixels = _canvas("light")

    with pytest.raises(GraphPixelAnalysisError) as captured:
        analyze_graph_viewport_pixels(
            pixels=bytes(pixels),
            pixel_width=width,
            pixel_height=height,
            point_pixel_scale=2,
            capture_content_frame={"x": 0, "y": 0, "width": width, "height": height},
            viewport_frame={"x": 20, "y": 20, "width": 260, "height": 140},
            appearance="light",
            zoom_state="fit",
            expected_kind_counts={"skill": 1},
        )

    assert captured.value.code == "graph_screenshot_geometry_mismatch"


def test_fit_fails_closed_when_palette_extent_does_not_use_either_viewport_axis() -> None:
    width, height, background, pixels = _canvas("light")
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="skill",
        x=132,
        y=60,
    )
    _paint_fit_relation_shells(
        pixels=pixels,
        width=width,
        background=background,
        profile_x=100,
        workflow_x=170,
        profile_y=48,
        workflow_y=88,
    )

    with pytest.raises(GraphPixelAnalysisError) as captured:
        _analyze(
            pixels=bytes(pixels),
            width=width,
            height=height,
            appearance="light",
            zoom_state="fit",
            expected_kind_counts={"skill": 1},
        )

    assert captured.value.code == "graph_fit_largest_dimension_occupancy_insufficient"


def test_fit_accepts_a_naturally_tall_cluster_without_horizontal_stretch() -> None:
    width, height, background, pixels = _canvas("light")
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="skill",
        x=132,
        y=28,
    )
    _paint_node(
        pixels=pixels,
        width=width,
        background=background,
        kind="agent",
        x=132,
        y=112,
    )
    _paint_fit_relation_shells(
        pixels=pixels,
        width=width,
        background=background,
        profile_x=112,
        workflow_x=158,
    )

    observed = _analyze(
        pixels=bytes(pixels),
        width=width,
        height=height,
        appearance="light",
        zoom_state="fit",
        expected_kind_counts={"skill": 1, "agent": 1},
    )

    assert observed["largest_dimension_occupancy"] >= 0.75
    assert observed["node_extent_frame"]["width"] < observed["viewport_frame"]["width"] * 0.75
    assert observed["node_crop_detected"] is False
