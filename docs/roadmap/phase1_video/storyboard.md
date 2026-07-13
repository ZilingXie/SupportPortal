# SupportPortal Phase 1 3-minute video storyboard

Goal: turn `phase1.html` into a 3-minute product ad. Start with a concrete customer failure, then show how Phase 1 changes support from manual after-the-fact review into AI-native quality control.

| Shot | Time | Asset | Treatment | Purpose |
|---|---:|---|---|---|
| 01 · Hook | 00:00-00:10 | `phase1_video/1-intro.jpeg` | Quick title hold, then zoom toward the hook question. | Ask: how do we ensure support reply quality before the customer sees the answer? |
| 02 · Bad case failure | 00:10-00:28 | `phase1_video/bad-case-support-failure-demo.mp4` | Play the full bad-case animation. | Show the pain before explaining it. |
| 03 · Pain points | 00:28-00:45 | `phase1.html#reply-quality` | Hold on the bad-case risk cards: Quality risk, Customer trust breaks, No operational help, After-the-fact visibility. | Explain reply quality, manager visibility, after-the-fact review, and late replies. |
| 04 · Why now | 00:45-01:05 | `phase1_video/2-why-now.png` | Slow zoom toward 73k and Custom. | Explain Zendesk cost plus limited customization space. |
| 05 · AI-native system | 01:05-01:25 | `phase1_video/3-big-pic.png` | Highlight Customer / Zendesk, SupportPortal Core, and Future Agent Network. | Show customer UI unchanged and internal workflow upgraded. |
| 06 · Assignment | 01:25-01:45 | `phase1_video/4-admin.png` plus `/workspace` if screen capture is available. | Highlight engineer intake, assignment admin, shifts, rules, fallback. | Show how support engineers receive cases and how managers control routing. |
| 07 · Guardrail showcase | 01:45-02:05 | `phase1_video/showcase-guardrail-demo.mp4` or `phase1_video/7-show-case.png` | Play Guardrail showcase dialogue. | Demonstrate how Guardrail rejects incomplete replies before customers see them. |
| 08 · Dashboard | 02:05-02:25 | `phase1_video/12-dashboard.png` | Pan across metrics and roadmap. | Show live quality control metrics, not only SLA reports. |
| 09 · Account automation | 02:25-02:43 | `/account` | Show account intake / billing route automation. | Demonstrate AI account automation for billing cases that do not need engineer in the loop. |
| 10 · R&D Agent | 02:43-02:55 | `phase1.html#rnd-investigation` | Zoom into the R&D Agent example: `Client.unpublish`, evidence list, abnormal-disconnection exclusion. | Show AI Agent evidence investigation instead of guessing. |
| 11 · AgentRelay vision | 02:55-03:00 | `phase1_video/11-phase1-closing.png` | Final hold with AgentRelay productization message. | Close with the bigger plan: each agent can represent a person or a team in productized cross-environment collaboration. |

## Asset Notes

- `phase1_video/1-intro.jpeg`: title and hook.
- `phase1_video/bad-case-support-failure-demo.mp4`: Bad case traditional support failure animation.
- `phase1_video/2-why-now.png`: Why now section.
- `phase1_video/3-big-pic.png`: architecture and customer UI unchanged.
- `phase1_video/4-admin.png`: `/workspace/admin`; optionally record `/workspace` for engineer intake.
- `phase1_video/5-agent-relay.png`: AgentRelay network screenshot; use as a fallback or overlay during the final productization message.
- `phase1_video/showcase-guardrail-demo.mp4`: Guardrail showcase animation.
- `phase1_video/7-show-case.png`: static fallback for Guardrail showcase dialogue.
- `/account`: AI account automation / billing case handling.
- `phase1.html#rnd-investigation`: R&D Agent dialogue, including `Client.unpublish` evidence and abnormal-disconnection exclusion.
- `phase1_video/11-phase1-closing.png`: AgentRelay vision closing frame.
- `phase1_video/12-dashboard.png`: Dashboard metrics.

## Post-production Notes

- Generate subtitles from `voiceover-jianying.txt`.
- If TTS pronounces product terms awkwardly, spell `AgentRelay` as "Agent Relay", `guardrail` as "guard rail", and `Zendesk` as "Zen desk" in the TTS editor only.
- Keep music light and confident. The proof should come from real UI, concrete failure, and before-send quality control.
