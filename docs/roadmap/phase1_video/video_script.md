# SupportPortal Phase 1 3-minute video script

Purpose: use this as an advertising-style product video script, not a traditional presentation script. Open with a problem, show the bad-case failure, then reveal how SupportPortal Phase 1 changes the operating model.

## Detailed scene plan

Use this table directly in Jianying. Each scene has one visual job, one screenshot / asset, and one narration beat.

| Scene | Time | Duration | Screenshot / asset | Visual action | Jianying placement | Voiceover |
|---|---:|---:|---|---|---|---|
| Scene 01 | 00:00-00:05 | 5s | `phase1_video/1-intro.jpeg` | Hard cut from black. Show product title and keep the question centered. | Main track image. Add slow zoom-in from 100% to 108%. | How do we ensure support reply quality before the customer sees the answer? |
| Scene 02 | 00:05-00:10 | 5s | `phase1_video/1-intro.jpeg` | Push closer to the hook question; keep background calm. | Same image, split clip at 5s, add subtitle emphasis on `reply quality`. | What if the customer only tells us the damage after trust is already broken? |
| Scene 03 | 00:10-00:18 | 8s | `phase1_video/bad-case-support-failure-demo.mp4` | Play the first part of the bad-case chat: customer black-screen issue and engineer guess. | Main track video. Keep original animation timing. | A customer reports a live-stream black screen. The engineer answers quickly, but guesses: the camera might be broken. |
| Scene 04 | 00:18-00:28 | 10s | `phase1_video/bad-case-support-failure-demo.mp4` | Continue the bad-case chat until the urgent workaround request appears. | Continue same video. Add red caption highlight on `no proof` and `no workaround`. | The customer asks for proof, asks what to do now, and asks for an immediate workaround because the stream is still ongoing. |
| Scene 05 | 00:28-00:36 | 8s | `https://support.stellarix.space/roadmap/phase1.html#reply-quality` or screenshot of the bad-case risk cards | Hold on Quality risk and Customer trust breaks. | Use page screenshot if available; otherwise crop from `phase1.html#reply-quality`. | That is the traditional support trap: reply quality is hard to guarantee. |
| Scene 06 | 00:36-00:45 | 9s | `https://support.stellarix.space/roadmap/phase1.html#reply-quality` | Pan across Manager, Review, and Speed pain cards. | Add three quick text callouts: manager visibility, after-the-fact review, late replies. | Manager visibility depends on sampling; after-the-fact review starts only after a bad rating, escalation, or complaint; and late replies make every weak answer feel even worse. |
| Scene 07 | 00:45-00:55 | 10s | `phase1_video/2-why-now.png` | Zoom into `73k` and Zendesk renewal context. | Main track image. Add subtle scale and a thin highlight box around `73k`. | For the past few years, we built our workflow around Zendesk. Zendesk license renewal is about seventy-three thousand dollars a year. |
| Scene 08 | 00:55-01:05 | 10s | `phase1_video/2-why-now.png` | Move from cost to customization/data mining limitations. | Same image, split clip, add highlight around `Custom`. | But the bigger issue is limited customization space: Zendesk does not give us enough customization space for feature extension, internal workflow customization, data mining, and quality analytics to move at the pace our support team needs. |
| Scene 09 | 01:05-01:15 | 10s | `phase1_video/3-big-pic.png` | Show Customer / Zendesk layer first. | Main track image. Add arrow highlight from Customer to Zendesk. | Now we want an AI-native ticket system that solves the root problem. The customer entry point stays unchanged. The customer UI stays unchanged. |
| Scene 10 | 01:15-01:25 | 10s | `phase1_video/3-big-pic.png` | Highlight SupportPortal Core: routing, assignment, guardrail, approval, dashboard. | Same image, split clip, pan down or crop into SupportPortal Core. | Internally, Zendesk cases flow into SupportPortal, where routing, assignment, guardrail, final approval, dashboard, case replay, and future agent collaboration become one controllable workflow. |
| Scene 11 | 01:25-01:35 | 10s | `https://support.stellarix.space/assignment` | Show engineer assignment workspace. | Screen capture or screenshot of `/assignment`; if missing, use browser capture. | Open the assignment workspace. Support engineers still receive and own cases. |
| Scene 12 | 01:35-01:45 | 10s | `phase1_video/4-admin.png` or `https://support.stellarix.space/assignment/admin` | Show assignment admin controls: shifts, rules, fallback. | Main track image. Add cursor-like motion over shift/rule controls. | But the system now decides route, owner, review requirement, SLA risk, and fallback. `/assignment/admin` gives managers the control surface for shifts, rules, and manual override. |
| Scene 13 | 01:45-01:53 | 8s | `phase1_video/showcase-guardrail-demo.mp4` | Play the first half of Guardrail demo: bad engineer draft and rejection. | Main track video. Keep progress indicator visible. | This is the guardrail moment. The engineer writes, "the camera is broken." AI Guardrail rejects the incomplete reply because it has no proof, no safe next step, and an unsafe root-cause claim. |
| Scene 14 | 01:53-02:05 | 12s | `phase1_video/showcase-guardrail-demo.mp4` or `phase1_video/7-show-case.png` | Continue to evidence accepted and final conservative draft. | Continue video; if using static fallback, crop to evidence and draft panels. | After Web SDK log evidence is added, `[websdk] no input frame received`, the system prepares a conservative customer draft. |
| Scene 15 | 02:05-02:16 | 11s | `phase1_video/12-dashboard.png` | Pan across AI guardrail pass rate and quality metrics. | Main track image. Add moving highlight over metrics. | Dashboard changes management from after-the-fact review to live quality control. We still track first response time, second response time, and SLA. |
| Scene 16 | 02:16-02:25 | 9s | `phase1_video/12-dashboard.png` | Highlight new metrics: agent interaction count, first-contact resolution, reject reasons, route accuracy. | Same image, split clip. Add keyword subtitles. | But Phase 1 also tracks AI guardrail pass rate, agent interaction count, first-contact resolution, reject reasons, route accuracy, and cases that should not be automated. |
| Scene 17 | 02:25-02:43 | 18s | `https://support.stellarix.space/account` | Show account intake / billing automation path. | Screen capture or screenshot of `/account`; use gentle pan through fields and status. | Some billing and account cases do not need an engineer in the loop. AI account automation can collect the right fields, classify invoice, account fraud, deactivate, or company verification cases, and move safe cases forward without dragging every request through manual handling. |
| Scene 18 | 02:43-02:55 | 12s | `https://support.stellarix.space/roadmap/phase1.html#rnd-investigation` | Zoom into R&D Agent dialogue, then highlight `Client.unpublish` evidence. | Page screenshot or screen capture. Add yellow highlight over `Client.unpublish`. | For hard technical cases, the Support Agent should not guess. It can ask an R&D Agent for evidence. In this example, the R&D Agent checks event data, finds a successful `Client.unpublish`, and concludes the screen-share stop was intentional application behavior, not an abnormal disconnection. |
| Scene 19 | 02:55-03:00 | 5s | `phase1_video/11-phase1-closing.png` | Final hold on AgentRelay vision and SupportPortal Phase 1 close. | Main track image. No fast motion; let the message land. | And we plan big: productize and protocolize AgentRelay for cross-environment agent collaboration. Each agent can represent a person or a team, and agents owned by different people, including agents with no public IP, can solve problems together with minimal configuration. |

## Coarse narration blocks

Keep this section as a backup if Jianying needs longer text chunks.

| Time | Visual | Voiceover |
|---|---|---|
| 00:00-00:10 | `phase1_video/1-intro.jpeg`, hard cut to product title, then a quick zoom into the question text. | How do we ensure support reply quality before the customer sees the answer? What if the customer only tells us the damage after trust is already broken? |
| 00:10-00:28 | `phase1_video/bad-case-support-failure-demo.mp4`, play the customer / engineer bad case animation. | A customer reports a live-stream black screen. The engineer answers quickly, but guesses: the camera might be broken. The customer asks for proof, asks what to do now, and asks for an immediate workaround because the stream is still ongoing. |
| 00:28-00:45 | `phase1.html#reply-quality`, hold on Bad case status cards: Quality risk, Customer trust breaks, No operational help, After-the-fact visibility. | That is the traditional support trap: reply quality is hard to guarantee; manager visibility depends on sampling; after-the-fact review starts only after a bad rating, escalation, or complaint; and late replies make every weak answer feel even worse. |
| 00:45-01:05 | `phase1_video/2-why-now.png`, zoom into 73k and Custom. | For the past few years, we built our workflow around Zendesk. Zendesk license renewal is about seventy-three thousand dollars a year, but the bigger issue is limited customization space: Zendesk does not give us enough customization space for feature extension, internal workflow customization, data mining, and quality analytics to move at the pace our support team needs. |
| 01:05-01:25 | `phase1_video/3-big-pic.png`, show Customer / Zendesk, SupportPortal Core, and Future Agent Network. | Now we want an AI-native ticket system that solves the root problem. The customer entry point stays unchanged. The customer UI stays unchanged. Internally, Zendesk cases flow into SupportPortal, where routing, assignment, guardrail, final approval, dashboard, case replay, and future agent collaboration become one controllable workflow. |
| 01:25-01:45 | `phase1_video/4-admin.png`, then briefly show `/assignment`. | Open the assignment workspace. Support engineers still receive and own cases, but the system now decides route, owner, review requirement, SLA risk, and fallback. `/assignment/admin` gives managers the control surface for shifts, rules, and manual override. |
| 01:45-02:05 | `phase1_video/7-show-case.png` or `phase1_video/showcase-guardrail-demo.mp4`, play the guardrail sequence. | This is the guardrail moment. The engineer writes, "the camera is broken." AI Guardrail rejects the incomplete reply because it has no proof, no safe next step, and an unsafe root-cause claim. After Web SDK log evidence is added, `[websdk] no input frame received`, the system prepares a conservative customer draft. |
| 02:05-02:25 | `phase1_video/12-dashboard.png`, focus on metrics. | Dashboard changes management from after-the-fact review to live quality control. We still track first response time, second response time, and SLA, but Phase 1 also tracks AI guardrail pass rate, agent interaction count, first-contact resolution, reject reasons, route accuracy, and cases that should not be automated. |
| 02:25-02:43 | `/account`, show account intake / billing automation path. | Some billing and account cases do not need an engineer in the loop. AI account automation can collect the right fields, classify invoice, account fraud, deactivate, or company verification cases, and move safe cases forward without dragging every request through manual handling. |
| 02:43-02:55 | `phase1.html#rnd-investigation`, zoom into R&D Agent example and `Client.unpublish` evidence. | For hard technical cases, the Support Agent should not guess. It can ask an R&D Agent for evidence. In this example, the R&D Agent checks event data, finds a successful `Client.unpublish`, and concludes the screen-share stop was intentional application behavior, not an abnormal disconnection. |
| 02:55-03:00 | `phase1_video/11-phase1-closing.png`, hold on AgentRelay vision. | And we plan big: productize and protocolize AgentRelay for cross-environment agent collaboration. Each agent can represent a person or a team, and agents owned by different people, including agents with no public IP, can solve problems together with minimal configuration. |

Combined closing segment for editors: 02:43-03:00 covers R&D Agent evidence plus the AgentRelay vision.

## Screenshot checklist

- `phase1_video/1-intro.jpeg`: opening title and hook.
- `phase1_video/bad-case-support-failure-demo.mp4`: bad support quality failure animation.
- `phase1_video/2-why-now.png`: Zendesk cost and customization limits.
- `phase1_video/3-big-pic.png`: customer UI unchanged and SupportPortal Core.
- `https://support.stellarix.space/assignment`: engineer assignment workspace screenshot or screen capture.
- `phase1_video/4-admin.png` or `https://support.stellarix.space/assignment/admin`: assignment admin controls.
- `phase1_video/showcase-guardrail-demo.mp4`: guardrail rejection animation.
- `phase1_video/7-show-case.png`: static fallback for guardrail showcase.
- `phase1_video/12-dashboard.png`: dashboard metrics.
- `https://support.stellarix.space/account`: account automation screenshot or screen capture.
- `https://support.stellarix.space/roadmap/phase1.html#rnd-investigation`: R&D Agent dialogue and `Client.unpublish` evidence.
- `phase1_video/11-phase1-closing.png`: closing AgentRelay vision.

## Editing Notes

- Use `bad-case-support-failure-demo.mp4` before any explanation. Let the failure create the need.
- Use `showcase-guardrail-demo.mp4` as the mirror image: the same bad answer is stopped before it reaches the customer.
- Keep these subtitle keywords on screen: `reply quality`, `manager visibility`, `after-the-fact review`, `AI-native ticket system`, `AI guardrail pass rate`, `AI account automation`, `AgentRelay`, `cross-environment agent collaboration`.
- Keep the tone crisp and product-led. Avoid "I will introduce" language.
