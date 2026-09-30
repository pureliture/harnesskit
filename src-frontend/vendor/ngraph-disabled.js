/**
 * HarnessKit deliberately fixes 3d-force-graph to its D3 engine.
 *
 * three-forcegraph imports both engines eagerly, while ngraph generates
 * functions dynamically. The packaged WebView forbids that code path, so the
 * browser bundle resolves the unused ngraph imports to this fail-closed shim.
 */
export default function disabledNgraphEngine() {
  throw new Error("The ngraph force engine is not bundled; use the D3 engine");
}
