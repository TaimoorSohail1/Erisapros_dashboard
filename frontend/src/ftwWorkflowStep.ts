export type FTWWorkflowStepState = {
  detail: "No changes needed" | "Send selected changes" | "Verified";
  state: "active" | "done";
};

export function ftwWorkflowStep({
  automationCompleted,
  currentQuerySucceeded,
  needsDecisionCount,
  verifiedUpdate,
}: {
  automationCompleted: boolean;
  currentQuerySucceeded: boolean;
  needsDecisionCount: number;
  verifiedUpdate: boolean;
}): FTWWorkflowStepState {
  if (verifiedUpdate) return { detail: "Verified", state: "done" };
  if (automationCompleted && currentQuerySucceeded && needsDecisionCount === 0) {
    return { detail: "No changes needed", state: "done" };
  }
  return { detail: "Send selected changes", state: "active" };
}
