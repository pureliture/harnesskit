export const DEFAULT_LEFT_WIDTH_PX = 304;
export const DEFAULT_RIGHT_WIDTH_PX = 368;
export const MIN_LEFT_WIDTH_PX = 200;
export const MAX_LEFT_WIDTH_PX = 512;
export const MIN_RIGHT_WIDTH_PX = 184;
export const MAX_RIGHT_WIDTH_PX = 560;
export const MIN_CENTER_WIDTH_PX = 480;
export const DIVIDER_WIDTH_PX = 12;
export const KEYBOARD_STEP_PX = 16;
export const THREE_PANE_MIN_EXCLUSIVE_PX = 1056;
export const STACKED_MAX_PX = 864;

const DEFAULT_STATE = Object.freeze({
  preferredLeftWidthPx: DEFAULT_LEFT_WIDTH_PX,
  preferredRightWidthPx: DEFAULT_RIGHT_WIDTH_PX,
  leftCollapsed: false,
  rightCollapsed: false,
  revision: 0,
  persisted: false,
});

function isIntegerWithin(value, minimum, maximum) {
  return Number.isSafeInteger(value) && value >= minimum && value <= maximum;
}

function normalizePreferredPair(left, right) {
  if (!isIntegerWithin(left, MIN_LEFT_WIDTH_PX, MAX_LEFT_WIDTH_PX)
    || !isIntegerWithin(right, MIN_RIGHT_WIDTH_PX, MAX_RIGHT_WIDTH_PX)) {
    return [DEFAULT_LEFT_WIDTH_PX, DEFAULT_RIGHT_WIDTH_PX];
  }
  return [left, right];
}

export function normalizeWorkspaceLayoutState(value) {
  const candidateLeft = value?.preferred_left_width_px ?? value?.preferredLeftWidthPx;
  const candidateRight = value?.preferred_right_width_px ?? value?.preferredRightWidthPx;
  const [preferredLeftWidthPx, preferredRightWidthPx] = normalizePreferredPair(
    candidateLeft,
    candidateRight,
  );
  const leftCollapsed = value?.left_collapsed === true || value?.leftCollapsed === true;
  const rightCollapsed = value?.right_collapsed === true || value?.rightCollapsed === true;
  if (!value
    || preferredLeftWidthPx !== candidateLeft
    || preferredRightWidthPx !== candidateRight
    || !Number.isSafeInteger(value.revision)
    || value.revision < 0
    || typeof value.persisted !== "boolean") {
    return { ...DEFAULT_STATE };
  }
  return {
    preferredLeftWidthPx,
    preferredRightWidthPx,
    leftCollapsed,
    rightCollapsed,
    revision: value.revision,
    persisted: value.persisted,
  };
}

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function distributeShrink(preferredLeft, preferredRight, sideBudget) {
  const excess = preferredLeft + preferredRight - sideBudget;
  if (excess <= 0) return [preferredLeft, preferredRight];

  const leftCapacity = preferredLeft - MIN_LEFT_WIDTH_PX;
  const rightCapacity = preferredRight - MIN_RIGHT_WIDTH_PX;
  const totalCapacity = leftCapacity + rightCapacity;
  const leftExact = (excess * leftCapacity) / totalCapacity;
  const rightExact = (excess * rightCapacity) / totalCapacity;
  let leftShrink = Math.floor(leftExact);
  let rightShrink = Math.floor(rightExact);
  let remainder = excess - leftShrink - rightShrink;
  const priorities = [
    { side: "left", fraction: leftExact - leftShrink },
    { side: "right", fraction: rightExact - rightShrink },
  ].sort((left, right) => right.fraction - left.fraction || (left.side === "left" ? -1 : 1));
  for (const candidate of priorities) {
    if (remainder === 0) break;
    if (candidate.side === "left" && leftShrink < leftCapacity) leftShrink += 1;
    if (candidate.side === "right" && rightShrink < rightCapacity) rightShrink += 1;
    remainder -= 1;
  }
  return [preferredLeft - leftShrink, preferredRight - rightShrink];
}

export function projectWorkspaceLayout({
  shellWidth,
  preferredLeftWidthPx,
  preferredRightWidthPx,
  activeSide = null,
  activeWidthPx = null,
  oppositeEffectiveWidthPx = null,
} = {}) {
  if (!Number.isSafeInteger(shellWidth) || shellWidth <= 0) {
    throw new Error("Workspace shell width must be a positive integer CSS pixel value.");
  }
  const [preferredLeft, preferredRight] = normalizePreferredPair(
    preferredLeftWidthPx,
    preferredRightWidthPx,
  );

  if (shellWidth <= STACKED_MAX_PX) {
    return Object.freeze({
      mode: "stacked",
      preferredLeftWidthPx: preferredLeft,
      preferredRightWidthPx: preferredRight,
      effectiveLeftWidthPx: shellWidth,
      effectiveRightWidthPx: shellWidth,
      centerWidthPx: shellWidth,
      leftMinimumPx: shellWidth,
      leftMaximumPx: shellWidth,
      rightMinimumPx: shellWidth,
      rightMaximumPx: shellWidth,
      separatorsActive: false,
    });
  }

  if (shellWidth <= THREE_PANE_MIN_EXCLUSIVE_PX) {
    const leftMaximum = Math.max(MIN_LEFT_WIDTH_PX, shellWidth - MIN_CENTER_WIDTH_PX);
    const effectiveLeft = clamp(preferredLeft, MIN_LEFT_WIDTH_PX, leftMaximum);
    const columnWidth = shellWidth - effectiveLeft;
    return Object.freeze({
      mode: "two-column",
      preferredLeftWidthPx: preferredLeft,
      preferredRightWidthPx: preferredRight,
      effectiveLeftWidthPx: effectiveLeft,
      effectiveRightWidthPx: columnWidth,
      centerWidthPx: columnWidth,
      leftMinimumPx: effectiveLeft,
      leftMaximumPx: effectiveLeft,
      rightMinimumPx: columnWidth,
      rightMaximumPx: columnWidth,
      separatorsActive: false,
    });
  }

  const sideBudget = shellWidth - (DIVIDER_WIDTH_PX * 2) - MIN_CENTER_WIDTH_PX;
  let [effectiveLeft, effectiveRight] = distributeShrink(
    preferredLeft,
    preferredRight,
    sideBudget,
  );

  if (activeSide === "left") {
    const oppositeMaximum = Math.max(
      MIN_RIGHT_WIDTH_PX,
      Math.min(MAX_RIGHT_WIDTH_PX, sideBudget - MIN_LEFT_WIDTH_PX),
    );
    const opposite = clamp(
      Number.isSafeInteger(oppositeEffectiveWidthPx) ? oppositeEffectiveWidthPx : effectiveRight,
      MIN_RIGHT_WIDTH_PX,
      oppositeMaximum,
    );
    effectiveRight = opposite;
    const activeMaximum = Math.max(
      MIN_LEFT_WIDTH_PX,
      Math.min(MAX_LEFT_WIDTH_PX, sideBudget - opposite),
    );
    effectiveLeft = clamp(
      Number.isSafeInteger(activeWidthPx) ? activeWidthPx : effectiveLeft,
      MIN_LEFT_WIDTH_PX,
      activeMaximum,
    );
  } else if (activeSide === "right") {
    const oppositeMaximum = Math.max(
      MIN_LEFT_WIDTH_PX,
      Math.min(MAX_LEFT_WIDTH_PX, sideBudget - MIN_RIGHT_WIDTH_PX),
    );
    const opposite = clamp(
      Number.isSafeInteger(oppositeEffectiveWidthPx) ? oppositeEffectiveWidthPx : effectiveLeft,
      MIN_LEFT_WIDTH_PX,
      oppositeMaximum,
    );
    effectiveLeft = opposite;
    const activeMaximum = Math.max(
      MIN_RIGHT_WIDTH_PX,
      Math.min(MAX_RIGHT_WIDTH_PX, sideBudget - opposite),
    );
    effectiveRight = clamp(
      Number.isSafeInteger(activeWidthPx) ? activeWidthPx : effectiveRight,
      MIN_RIGHT_WIDTH_PX,
      activeMaximum,
    );
  }

  return Object.freeze({
    mode: "three-pane",
    preferredLeftWidthPx: preferredLeft,
    preferredRightWidthPx: preferredRight,
    effectiveLeftWidthPx: effectiveLeft,
    effectiveRightWidthPx: effectiveRight,
    centerWidthPx: shellWidth - (DIVIDER_WIDTH_PX * 2) - effectiveLeft - effectiveRight,
    leftMinimumPx: MIN_LEFT_WIDTH_PX,
    leftMaximumPx: Math.min(MAX_LEFT_WIDTH_PX, sideBudget - effectiveRight),
    rightMinimumPx: MIN_RIGHT_WIDTH_PX,
    rightMaximumPx: Math.min(MAX_RIGHT_WIDTH_PX, sideBudget - effectiveLeft),
    separatorsActive: true,
  });
}
