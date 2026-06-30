# SupportPortal Phase 1 3-minute video script

Purpose: use this as an advertising-style product video script, not a traditional presentation script. Open with a problem, show the bad-case failure, then reveal how SupportPortal Phase 1 changes the operating model.

| Time | Visual | Voiceover |
|---|---|---|
| 00:00-00:10 | `phase1_video/1-intro.jpeg`, hard cut to product title, then a quick zoom into the question text. | What if a support reply is fast, but wrong? What if the customer only tells us the damage after trust is already broken? |
| 00:10-00:28 | `phase1_video/bad-case-support-failure-demo.mp4`, play the customer / engineer bad case animation. | A customer reports a live-stream black screen. The engineer answers quickly, but guesses: the camera might be broken. The customer asks for proof, asks what to do now, and asks for an immediate workaround because the stream is still ongoing. |
| 00:28-00:45 | `phase1.html#showcase`, hold on Bad case status cards: Quality risk, Customer trust breaks, No operational help, After-the-fact visibility. | That is the traditional support trap: reply quality is hard to guarantee; manager visibility depends on sampling; after-the-fact review starts only after a bad rating, escalation, or complaint; and late replies make every weak answer feel even worse. |
| 00:45-01:05 | `phase1_video/2-why-now.png`, zoom into 73k and Custom. | For the past few years, we built our workflow around Zendesk. Zendesk license renewal is about seventy-three thousand dollars a year, but the bigger issue is limited customization space: Zendesk does not give us enough customization space for feature extension, internal workflow customization, data mining, and quality analytics to move at the pace our support team needs. |
| 01:05-01:25 | `phase1_video/3-big-pic.png`, show Customer / Zendesk, SupportPortal Core, and Future Agent Network. | Now we want an AI-native ticket system that solves the root problem. The customer entry point stays unchanged. The customer UI stays unchanged. Internally, Zendesk cases flow into SupportPortal, where routing, assignment, guardrail, final approval, dashboard, case replay, and future agent collaboration become one controllable workflow. |
| 01:25-01:45 | `phase1_video/4-admin.png`, then briefly show `/assignment`. | Open the assignment workspace. Support engineers still receive and own cases, but the system now decides route, owner, review requirement, SLA risk, and fallback. `/assignment/admin` gives managers the control surface for shifts, rules, and manual override. |
| 01:45-02:05 | `phase1_video/7-show-case.png` or `phase1_video/showcase-guardrail-demo.mp4`, play the guardrail sequence. | This is the guardrail moment. The engineer writes, "the camera is broken." AI Guardrail rejects the incomplete reply because it has no proof, no safe next step, and an unsafe root-cause claim. After Web SDK log evidence is added, `[websdk] no input frame received`, the system prepares a conservative customer draft. |
| 02:05-02:25 | `phase1_video/12-dashboard.png`, focus on metrics. | Dashboard changes management from after-the-fact review to live quality control. We still track first response time, second response time, and SLA, but Phase 1 also tracks AI guardrail pass rate, agent interaction count, first-contact resolution, reject reasons, route accuracy, and cases that should not be automated. |
| 02:25-02:43 | `/account`, show account intake / billing automation path. | Some billing and account cases do not need an engineer in the loop. AI account automation can collect the right fields, classify invoice, account fraud, deactivate, or company verification cases, and move safe cases forward without dragging every request through manual handling. |
| 02:43-02:55 | `phase1.html#agentrelay`, zoom into R&D Agent example and `Client.unpublish` evidence. | For hard technical cases, the Support Agent should not guess. It can ask an R&D Agent for evidence. In this example, the R&D Agent checks event data, finds a successful `Client.unpublish`, and concludes the screen-share stop was intentional application behavior, not an abnormal disconnection. |
| 02:55-03:00 | `phase1_video/11-phase1-closing.png`, hold on AgentRelay vision. | And we plan big: productize and protocolize AgentRelay for cross-environment agent collaboration, so agents owned by different people, including agents with no public IP, can solve problems together with minimal configuration. |

Combined closing segment for editors: 02:43-03:00 covers R&D Agent evidence plus the AgentRelay vision.

## Editing Notes

- Use `bad-case-support-failure-demo.mp4` before any explanation. Let the failure create the need.
- Use `showcase-guardrail-demo.mp4` as the mirror image: the same bad answer is stopped before it reaches the customer.
- Keep these subtitle keywords on screen: `reply quality`, `manager visibility`, `after-the-fact review`, `AI-native ticket system`, `AI guardrail pass rate`, `AI account automation`, `AgentRelay`, `cross-environment agent collaboration`.
- Keep the tone crisp and product-led. Avoid "I will introduce" language.
