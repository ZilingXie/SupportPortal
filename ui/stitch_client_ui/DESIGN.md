# Design System Strategy: The Intelligent Concierge

## 1. Overview & Creative North Star
The Creative North Star for this design system is **"The Intelligent Concierge."** 

We are moving away from the "mechanical database" aesthetic typical of legacy ticketing systems. Instead, we are building a high-end, editorial-inspired workspace where AI doesn't just process data—it curates an experience. This system balances the authoritative weight of Deep Indigo (`secondary`) with the kinetic energy of Vibrant Light Blue (`primary_container`).

To break the "template" look, we employ **Intentional Asymmetry**. Dashboards should not be perfectly mirrored; use the `24` (5.5rem) spacing token to create expansive "breathing zones" for critical AI insights, while tucking secondary metadata into compact, high-density modules. We favor overlapping elements—such as chat citations that "float" slightly over the bubble boundary—to create a sense of three-dimensional space and organic collaboration.

---

## 2. Colors & Surface Philosophy
The color palette is designed to distinguish between **Stability** (Indigo) and **Action** (Light Blue).

*   **Primary (`#006493` / `#00B0FF`):** Reserved for high-intent actions, AI-driven suggestions, and active sentiment.
*   **Secondary (`#4c56af`):** Used for the "Architectural Frame"—navigation rails and structural headers.
*   **Surface Hierarchy:** We use a "Nested Depth" model.
    *   **The "No-Line" Rule:** 1px solid borders are strictly prohibited for sectioning. 
    *   **Tonal Transitions:** Define boundaries by placing a `surface_container_low` card on a `surface` background. For internal nested modules, move to `surface_container_highest`. 
*   **The Glass & Gradient Rule:** For main AI action triggers, use a linear gradient from `primary` to `primary_container`. For floating AI panels, use Glassmorphism: `surface_variant` at 60% opacity with a `20px` backdrop-blur. This ensures the AI feels like a "lens" over the data, not a box inside it.

---

## 3. Typography: Editorial Authority
We utilize a dual-typeface system to create a sophisticated, data-rich hierarchy.

*   **Display & Headlines (Manrope):** We use Manrope for all `display` and `headline` levels. Its geometric yet warm curves provide a premium, modern feel. Use `headline-lg` for ticket titles to give them an editorial presence.
*   **Body & Labels (Inter):** Inter is our workhorse for clarity. Use `body-md` for ticket descriptions and `label-sm` for technical metadata. 
*   **Contrast as Hierarchy:** Pair a `headline-sm` title (Bold, `on_surface`) directly with a `label-md` timestamp (Regular, `outline`). The dramatic shift in scale and weight replaces the need for dividers.

---

## 4. Elevation & Depth
Depth is a functional tool, not a decoration. We achieve this through **Tonal Layering**.

*   **The Layering Principle:** 
    *   Background: `surface`
    *   Main Content Area: `surface_container_low`
    *   Individual Cards/Tiles: `surface_container_lowest` (White)
    *   This "inverted lift" creates a natural, soft focus on the content.
*   **Ambient Shadows:** For floating elements (like AI citation popovers), use an extra-diffused shadow: `offset-y: 8px, blur: 24px, color: alpha(on_secondary_fixed_variant, 0.08)`. Never use pure black or grey shadows.
*   **The Ghost Border:** If a boundary is required for accessibility, use `outline_variant` at 15% opacity. It should be felt, not seen.

---

## 5. Components

### AI-Integrated Chat Bubbles
*   **Visual Style:** User bubbles use `surface_container_high`. AI bubbles use a subtle `primary_fixed` tint.
*   **Citation Tags:** Small, `label-sm` pills nested at the bottom-right of a bubble. Use `secondary_fixed` for the background to denote "Source Truth."
*   **Layout:** Use `xl` (0.75rem) roundedness for the outer corners, but `sm` (0.125rem) for the corner originating the message to create a "tail" effect.

### Sentiment Meters
*   **Design:** A horizontal track using `surface_container_highest`. The "indicator" is a `6px` thick line using a gradient from `error` (Negative) to `primary_container` (Positive). 
*   **Context:** Place these in the `title-sm` area of a ticket header to provide instant emotional context without reading the text.

### Hybrid Status Badges
*   **AI vs. Human:** Use a "split pill" design. The left icon indicates the agent type (Sparkle for AI, User for Human) using `on_tertiary_container`, while the right text indicates the status (e.g., "Analyzing" or "Responding").
*   **Rounding:** Always `full` (9999px) for status badges to contrast against the `lg` (0.5rem) roundedness of containers.

### The 'AI Managing' Toggle
*   **The Signature Component:** A large, tactile switch. When "AI Managing" is active, the track glows with a `primary_container` inner shadow and the handle features a subtle `surface_tint` pulse.
*   **Haptics:** Label the states clearly using `label-md` in all-caps for an authoritative, "Control Center" feel.

### Cards & Lists
*   **The Divider Ban:** Never use horizontal lines to separate tickets. Use `spacing.5` (1.1rem) of vertical whitespace and a subtle shift from `surface_container_low` to `surface_container` on hover to indicate interactivity.

---

## 6. Do's and Don'ts

### Do
*   **Do** use asymmetrical margins (e.g., more space on the left than the right) to guide the eye toward AI-generated insights.
*   **Do** use `tertiary_container` for "Success" or "Resolved" states to maintain a sophisticated palette that avoids "Stoplight" (Red/Green) clichés.
*   **Do** lean into `surface_bright` for the main workspace to keep the UI feeling energized and professional.

### Don'ts
*   **Don't** use 100% opaque borders. They clutter the data and make the system feel "boxed in."
*   **Don't** use standard blue for everything. Reserve the vibrant `#00B0FF` (`primary_container`) for "The AI's Voice" and critical CTAs.
*   **Don't** use drop shadows on nested cards. Use background color shifts instead. Reserve shadows for elements that literally "pop over" the UI (Modals, Tooltips).