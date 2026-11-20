# Earnings Intelligence — Product

## Users
- **Primary:** sell-side and buy-side equity analysts and portfolio managers covering US-listed stocks.
  They prepare for earnings prints, react to them within minutes, and defend their view to a PM or
  investment committee.
- **Secondary:** risk and compliance reviewers. They approve research before it is shared and need an audit trail.

## Jobs to be done
1. **Before the print:** "Tell me where the bar is, what's priced in, and what would move the stock, so I
   can size the risk and prepare my questions."
2. **At the print:** "Tell me within minutes whether this was a real beat (quality, guidance, cash) or a
   low-quality one."
3. **After the print:** "Show me how the market is reacting versus history and the implied move, and what
   changed in management's language and risk disclosures."
4. **Over time:** "Show me whether this tool's forward-looking calls have been right, so I know how much to trust them."

## Principles
- **Every number has a source.** Narrative numbers come only from cited facts. Hovering any number shows
  its source and as-of time. Anything uncited is removed before publishing.
- **Show what's missing.** Unavailable data is shown as unavailable, with what would make it available.
  It is never estimated silently.
- **No look-ahead.** A brief "as of" a date uses only data that existed on that date.
- **Honest uncertainty.**
  - Probabilities come from base rates and are labelled as such. Confidence is explained in words.
  - Nothing is called "calibrated" until the back-test supports it.
- **Research, not advice.**
  - No buy/sell/hold, no price targets, no personal recommendations, and a disclaimer on every output.
  - Requests for advice get a polite research-only redirect.
- **Human in the loop.** Reports wait for review. Every approval or rejection is recorded with notes in an append-only audit log.

## Non-goals (for now)
- Trading, order routing, portfolio construction.
- Non-US listings and IFRS-only filers (shown with explicit gaps).
- Redistribution of licensed third-party data.
