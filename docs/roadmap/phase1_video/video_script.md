# SupportPortal Phase 1 3-minute video script

Paste this script directly into Jianying.

How do we ensure support reply quality before the customer sees the answer? What if the customer only tells us the damage after trust is already broken?

A customer reports a live-stream black screen. The engineer answers quickly, but guesses: the camera might be broken. The customer asks for proof, asks what to do now, and asks for an immediate workaround.

That is the traditional support trap: reply quality is hard to guarantee, manager visibility depends on sampling, review starts after a bad rating or complaint, and late replies make every weak answer worse.

For years, we built support around Zendesk. The license renewal is about seventy-three thousand dollars a year, but the bigger issue is limited customization space: features, internal workflow, data mining, and quality analytics cannot move at the pace our team needs.

Now we want an AI-native ticket system that solves the root problem. The customer entry point stays unchanged. The customer UI stays unchanged. Internally, Zendesk cases flow into SupportPortal, where routing, assignment, guardrail, final approval, dashboard, case replay, and future agent collaboration become one controllable workflow.

Open the assignment workspace. Support engineers still receive and own cases, while SupportPortal manages route, owner, review need, SLA risk, and fallback.

This is the guardrail moment. The engineer writes, the camera is broken. AI Guardrail rejects the reply because it has no proof, no safe next step, and an unsafe root-cause claim. After Web SDK log evidence is added, the system prepares a conservative customer draft.

Dashboard changes management from after-the-fact review to live quality control. We still track first response time, second response time, and SLA. Phase 1 also tracks AI guardrail pass rate, agent interaction count, first-contact resolution, reject reasons, and route accuracy.

Some billing and account cases do not need an engineer in the loop. AI account automation collects fields, classifies invoice, fraud, deactivate, or verification cases, and moves safe cases forward without manual handling.

And we plan big: productize and protocolize AgentRelay for cross-environment agent collaboration. Each agent can represent a person or a team. Even agents without public IP can solve problems together with minimal configuration.

For hard technical cases, the Support Agent should not guess. It can ask an R&D Agent for evidence. In this example, the R&D Agent checks event data, finds a successful Client.unpublish call, and concludes the screen-share stop was intentional, not an abnormal disconnection.
