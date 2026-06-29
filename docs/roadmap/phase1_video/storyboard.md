# SupportPortal Phase 1 3-minute video storyboard

Goal: turn `phase1.html` into a 3-minute product explanation video. The style should feel like real system pages plus focused zooms, not a generic slide deck.

| Shot | Time | Asset | Treatment | Purpose |
|---|---:|---|---|---|
| 01 · Intro | 00:00-00:15 | `phase1_video/1-intro.jpeg` | Start on the Phase 1 title. | Establish the SupportPortal Phase 1 context. |
| 02 · Why now | 00:15-00:35 | `phase1_video/2-why-now.png` | Slow zoom toward 73k, SLA, Billing, and Custom. | Explain why now: Zendesk cost plus limited customization space. |
| 03 · Big picture | 00:35-01:05 | `phase1_video/3-big-pic.png` | Hold on Customer / Zendesk, SupportPortal Core, and Future Agent Network. | Show the conservative migration strategy: customer entry unchanged, internal handling upgraded. |
| 04 · Assignment admin | 01:05-01:25 | `phase1_video/4-admin.png` | Highlight engineer/day picker, shifts, manual override, or routing fallback. | Show that assignment is a dispatch control plane, not only a UI demo. |
| 05 · AgentRelay network | 01:25-01:55 | `phase1_video/5-agent-relay.png` | Show AgentRelay questions and communication state machine. | Explain why AgentRelay exists, why not pure A2A, and why agents need task-based communication. |
| 06 · R&D Agent dialogue | 01:55-02:20 | `phase1.html#agentrelay` | Zoom into the R&D Agent example: question, SQL verification query, and returned SID. | Demonstrate that R&D/Data Agents produce reusable evidence instead of free-form chat. |
| 07 · Guardrail showcase | 02:20-02:45 | `phase1_video/7-show-case.png` | First show the unsafe draft, then Guardrail rejected, then the safe draft. | Demonstrate how Guardrail rejects incomplete replies before customers see them. |
| 08 · Dashboard roadmap | 02:45-03:00 | `phase1_video/12-dashboard.png` | Move from Dashboard metrics to the three-stage roadmap. | Close on management metric upgrade and phased rollout. |
| Optional closing hold | after 03:00 | `phase1_video/11-phase1-closing.png` | Use as a final freeze frame if the exported video needs a closing beat. | Leave viewers with the Phase 1 success criterion. |

## Asset Notes

- `phase1_video/1-intro.jpeg`: title section from `docs/roadmap/phase1.html`.
- `phase1_video/2-why-now.png`: Why now section.
- `phase1_video/3-big-pic.png`: architecture and assignment summary.
- `phase1_video/4-admin.png`: `/assignment/admin`.
- `phase1_video/5-agent-relay.png`: AgentRelay section.
- `phase1.html#agentrelay`: R&D Agent dialogue, including SID evidence and SQL query.
- `phase1_video/7-show-case.png`: Guardrail showcase.
- `phase1_video/11-phase1-closing.png`: optional final closing frame.
- `phase1_video/12-dashboard.png`: Dashboard and roadmap closing view.

## Post-production Notes

- Generate subtitles from `voiceover-jianying.txt`.
- If TTS pronounces product terms awkwardly, spell `AgentRelay` as "Agent Relay" and `guardrail` as "guard rail" in the TTS editor only.
- Keep background music minimal; trust comes from real UI, evidence, and a clear operating model.
