const MODEL_EFFORT_HEADROOM = 24

export function formatModelStatusLabel(model: string, reasoningEffort?: string, availableWidth?: number): string {
  const trimmedModel = model.trim()
  const trimmedEffort = reasoningEffort?.trim()

  if (!trimmedModel || !trimmedEffort) {
    return trimmedModel
  }

  const withEffort = `${trimmedModel} (${trimmedEffort})`

  // Reserve room for the rest of the status line so the model name stays readable.
  if (availableWidth != null && availableWidth < withEffort.length + MODEL_EFFORT_HEADROOM) {
    return trimmedModel
  }

  return withEffort
}
