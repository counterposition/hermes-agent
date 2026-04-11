import { describe, expect, it } from 'vitest'

import { formatModelStatusLabel } from '../lib/status.js'

describe('formatModelStatusLabel', () => {
  it('appends reasoning effort when there is enough room', () => {
    expect(formatModelStatusLabel('gpt-5.4', 'high', 80)).toBe('gpt-5.4 (high)')
  })

  it('drops the effort suffix before squeezing the model label on narrow widths', () => {
    expect(formatModelStatusLabel('claude-sonnet-4', 'medium', 40)).toBe('claude-sonnet-4')
  })

  it('returns the model unchanged when no reasoning effort is provided', () => {
    expect(formatModelStatusLabel('claude-sonnet-4', undefined, 80)).toBe('claude-sonnet-4')
  })
})
