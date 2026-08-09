from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


class WorkspaceAdminUiContractTests(unittest.TestCase):
    def run_admin_app_script(self, script: str) -> None:
        node_script = textwrap.dedent(
            f"""
            (async () => {{
              const fs = require("fs");
              const vm = require("vm");
              const root = {{
                innerHTML: "",
                handlers: {{}},
                addEventListener(type, handler) {{ this.handlers[type] = handler; }},
                querySelector() {{ return null; }},
              }};
              const storage = new Map();
              const sandbox = {{
                console, Headers, URLSearchParams,
                window: {{ location: {{ pathname: "/workspace/admin/" }} }},
                document: {{ getElementById() {{ return root; }} }},
                localStorage: {{
                  getItem(key) {{ return storage.has(key) ? storage.get(key) : null; }},
                  setItem(key, value) {{ storage.set(key, String(value)); }},
                  removeItem(key) {{ storage.delete(key); }},
                }},
                fetch: async () => ({{ ok: true, status: 200, json: async () => ({{ cases: [] }}) }}),
                FormData: function FormData() {{ return {{ get() {{ return ""; }}, entries() {{ return []; }} }}; }},
              }};
              sandbox.globalThis = sandbox;
              vm.createContext(sandbox);
              vm.runInContext(fs.readFileSync("ui/workspace-ui/admin/app.js", "utf8"), sandbox);
              await vm.runInContext(`(async () => {{\\n${{{script!r}}}\\n}})()`, sandbox);
            }})().catch((error) => {{ console.error(error); process.exit(1); }});
            """
        )
        result = subprocess.run(
            ["node", "-e", node_script],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_workspace_admin_replaces_assignment_route(self) -> None:
        main_source = Path("backend/main.py").read_text(encoding="utf-8")
        self.assertIn('app.mount("/workspace", StaticFiles(directory=WORKSPACE_DIR, html=True)', main_source)
        self.assertNotIn('app.mount("/assignment"', main_source)
        self.assertFalse(Path("ui/assignment-ui").exists())

    def test_workspace_admin_uses_protected_production_apis(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        for marker in (
            "/api/workspace/auth/login",
            "/api/workspace/admin/accounts",
            "/api/workspace/admin/metrics",
            "/api/workspace/admin/audit",
            "/api/workspace/admin/dispatch",
            "/api/workspace/admin/reassign-due",
            "/api/workspace/admin/invitations",
            "/api/workspace/admin/engineer-schedules",
            "/api/workspace/cases?assignment_status=all",
            "data-invitation-form",
            "data-schedule-form",
            "On Schedule Now",
            "Weekly Schedule",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("supportportal_assignment_admin_schedule", source)
        self.assertNotIn('/api/engineer/tickets?status=all', source)
        self.assertNotIn("/availability", source)

    def test_account_automation_hierarchical_agent_config_and_environment_tabs_are_operational(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")
        for marker in (
            '"automated-cases"', '"agent-config"', '"environment-config"',
            "/api/workspace/admin/account-automation",
            "/api/workspace/admin/agent-config",
            "/api/workspace/admin/account-personas",
            "/api/workspace/admin/environment-config",
            "Automation share", "Current route", "Agent Config", "No MCP configured", "Configuration names",
            "data-action=\"toggle-agent-tree\"", "data-env-search", "data-action=\"select-agent-prompt\"",
            "environmentLoadError", "loadEnvironmentConfig",
            "agentConfigLoadError", "loadAgentConfig", "data-action=\"retry-agent-config\"",
            "data-action=\"retry-environment-config\"", "automation_personas", "route_navigation",
            "data-persona-draft-form", 'data-action="publish-persona"',
            'data-action="rollback-persona"', 'data-action="toggle-persona"',
            "description.toLowerCase()", "admin-config-description", "admin-config-copy",
        ):
            self.assertIn(marker, source)
        for marker in (".admin-metric-strip", ".admin-agent-workspace", ".admin-agent-tree", ".admin-agent-prompt-layout", ".admin-persona-workspace", ".admin-config-list", ".admin-config-description", ".admin-config-copy"):
            self.assertIn(marker, css)

        self.run_admin_app_script(
            """
            automationData = { metrics: { total_account_cases: 4, automated_cases: 1, not_automated_cases: 3, automation_rate: .25 }, cases: [{ client_ticket_id: 'TK-1', title: 'Invoice', automation_status: 'automation' }] };
            agentConfigData = {
              agents: [{ key: 'route-agent', kind: 'agent', name: 'Route Agent', description: 'Routes requests.', status: 'active', components: [], prompts: [{ key: 'automation-system', name: 'Automation Router', version: 'v1', component_key: 'automation-router', content: 'actual prompt', metadata: {} }], skills: [], mcp_servers: [] }],
              route_navigation: { key: 'route-agent', kind: 'agent', is_agent: true, name: 'Route Agent', description: 'Routes requests.', status: 'active', prompt_keys: [], capabilities: [], children: [
                { key: 'conversation-action', kind: 'outcome', is_agent: false, name: 'Conversation Action', description: 'Handles conversation.', status: 'active', prompt_keys: [], capabilities: [], children: [] },
                { key: 'agora-router', kind: 'router', is_agent: true, name: 'Agora Router', description: 'Routes Agora.', status: 'active', prompt_keys: [], capabilities: [], children: [
                  { key: 'agora-technical', kind: 'outcome', is_agent: false, name: 'Agora Technical', description: 'Routes technical cases.', status: 'active', prompt_keys: [], capabilities: [], children: [] },
                  { key: 'automation-router', kind: 'router', is_agent: true, name: 'Automation Router', description: 'Routes Automation.', status: 'active', persona_scope: 'account-automation', prompt_keys: ['automation-system'], capabilities: [], children: [
                    { key: 'detailed-invoice', kind: 'automation', is_agent: false, name: 'Detailed Invoice', description: 'Runs invoice behavior.', status: 'active', prompt_keys: [], capabilities: [{ key: 'billing', name: 'Billing Handler', description: 'Deterministic invoice behavior.', status: 'active' }], children: [] }
                  ] },
                  { key: 'agora-uncategorized', kind: 'handoff', is_agent: false, name: 'Human Review', description: 'Reviews uncertain cases.', status: 'active', prompt_keys: [], capabilities: [], children: [] }
                ] },
                { key: 'intent-uncertain', kind: 'handoff', is_agent: false, name: 'Human Review', description: 'Reviews uncertain intent.', status: 'active', prompt_keys: [], capabilities: [], children: [] }
              ] },
              route_runtime: { router_prompt_version: 'account-router-v1', stage_details: [{ name: 'intent_classifier', description: 'Classifies the request.' }] },
              automation_personas: [{ persona_key: 'default-support', display_name: 'Default Support', enabled: true, published_version: 1, versions: [{ version: 1, status: 'published', content: { instruction: 'Warm', opener: '', signature: 'Best,\\nSid\\nSupport Engineer 2' }, change_note: 'Initial' }] }]
            };
            environmentData = { names: ['OPENAI_API_KEY', 'TICKET_DB_DSN'], items: [
              { name: 'OPENAI_API_KEY', description: 'Credential used by OpenAI.' },
              { name: 'TICKET_DB_DSN', description: 'PostgreSQL connection string for ticket storage.' }
            ] };
            if (!renderAutomatedCases().includes('25.0%')) throw new Error('automation ratio missing');
            selectedAgentPath = [];
            const catalogMarkup = renderAgentConfig();
            if (!catalogMarkup.includes('Route Agent') || catalogMarkup.includes('Billing Automation') || catalogMarkup.includes('Related services')) throw new Error('top-level Agent catalog is invalid');
            selectedAgentPath = ['route-agent'];
            const routeMarkup = renderAgentConfig();
            if (!routeMarkup.includes('account-router-v1') || !routeMarkup.includes('Classifies the request.')) throw new Error('route runtime missing from Route Agent');
            if (!routeMarkup.includes('Conversation Action') || !routeMarkup.includes('Human Review') || !routeMarkup.includes('Agent')) throw new Error('Route Agent Overview outcomes missing');
            const treeMarkup = renderAgentTree(agentConfigData.agents);
            if (!treeMarkup.includes('Agora Router') || !treeMarkup.includes('Automation Router')) throw new Error('Agent-only tree is incomplete');
            if (treeMarkup.includes('Conversation Action') || treeMarkup.includes('Human Review') || treeMarkup.includes('Detailed Invoice')) throw new Error('non-Agent node leaked into tree');
            if (!routeMarkup.includes('admin-agent-route-outcome') || !routeMarkup.includes('admin-agent-badge is-agent')) throw new Error('Agent/outcome route semantics missing');
            const routeMobileMarkup = renderAgentMobileNav(agentConfigData.agents);
            if (!routeMobileMarkup.includes('Agora Router') || routeMobileMarkup.includes('Conversation Action') || routeMobileMarkup.includes('Automation Router')) throw new Error('mobile navigation must show only direct child Agents');
            selectedAgentPath = ['route-agent', 'agora-router', 'automation-router'];
            selectedAgentViews['automation-router'] = 'persona';
            const personaMarkup = renderAgentConfig();
            if (!personaMarkup.includes('Automation Persona') || !personaMarkup.includes('Default Support')) throw new Error('Automation Persona management missing');
            if (!personaMarkup.includes('name="signature"') || !personaMarkup.includes('Support Engineer 2') || personaMarkup.includes('Signoff name')) throw new Error('Persona Signature editor missing');
            selectedAgentViews['automation-router'] = 'overview';
            selectedAutomationBehaviorKey = 'detailed-invoice';
            const behaviorMarkup = renderAgentConfig();
            if (!behaviorMarkup.includes('Detailed Invoice') || !behaviorMarkup.includes('Billing Handler') || !behaviorMarkup.includes('aria-expanded="true"')) throw new Error('Automation behavior Overview is invalid');
            if (behaviorMarkup.includes('#agent-config/route-agent/agora-router/automation-router/detailed-invoice')) throw new Error('Automation behavior still creates a fifth-level URL');
            root.handlers.click({ target: { closest(selector) {
              if (selector === '[data-action]') return { dataset: { action: 'toggle-automation-behavior' } };
              if (selector === '[data-behavior-key]') return { dataset: { behaviorKey: 'detailed-invoice' } };
              return null;
            } } });
            if (selectedAutomationBehaviorKey !== '') throw new Error('Automation behavior disclosure did not collapse');
            selectedAgentPath = ['route-agent'];
            selectedAgentViews['route-agent'] = 'mcp';
            if (!renderAgentConfig().includes('No MCP configured.')) throw new Error('empty MCP state missing');
            if (!renderEnvironmentConfig().includes('OPENAI_API_KEY')) throw new Error('config name missing');
            if (!renderEnvironmentConfig().includes('Credential used by OpenAI.')) throw new Error('config description missing');
            environmentQuery = 'ticket storage';
            const filteredEnvironment = renderEnvironmentConfig();
            if (!filteredEnvironment.includes('TICKET_DB_DSN') || filteredEnvironment.includes('OPENAI_API_KEY')) throw new Error('description search missing');
            environmentQuery = '';
            environmentData = { names: ['LEGACY_ONLY_KEY'] };
            const legacyEnvironment = renderEnvironmentConfig();
            if (!legacyEnvironment.includes('LEGACY_ONLY_KEY') || !legacyEnvironment.includes('Description unavailable until the API is updated.')) throw new Error('names-only compatibility missing');
            globalThis.location = { hash: '#route-prompt' };
            if (sectionFromHash() !== 'overview') throw new Error('legacy route hash still resolves');
            globalThis.location = { hash: '#persona-prompts' };
            if (sectionFromHash() !== 'overview') throw new Error('legacy persona hash still resolves');
            globalThis.location = { hash: '#route-strategy' };
            if (sectionFromHash() !== 'agent-config' || agentPathFromHash()[0] !== 'route-agent') throw new Error('Route Strategy compatibility missing');
            globalThis.location = { hash: '#agent-config/route-agent/agora-router/automation-router' };
            if (sectionFromHash() !== 'agent-config' || agentPathFromHash().at(-1) !== 'automation-router') throw new Error('Agent Config deep link missing');
            globalThis.location = { hash: '#agent-config/route-agent/conversation-action' };
            if (agentPathFromHash().join('/') !== 'route-agent') throw new Error('route outcome deep link was not normalized');
            let replacedHash = '';
            globalThis.history = { replaceState(_state, _title, hash) { replacedHash = hash; } };
            normalizeAgentLocation(agentSelectionFromHash());
            if (replacedHash !== '#agent-config/route-agent') throw new Error('route outcome deep link did not replace the legacy hash');
            globalThis.location = { hash: '#agent-config/route-agent/agora-router/automation-router/detailed-invoice' };
            const behaviorSelection = agentSelectionFromHash();
            if (behaviorSelection.path.join('/') !== 'route-agent/agora-router/automation-router' || behaviorSelection.behaviorKey !== 'detailed-invoice') throw new Error('Automation behavior compatibility missing');
            normalizeAgentLocation(behaviorSelection);
            if (replacedHash !== '#agent-config/route-agent/agora-router/automation-router') throw new Error('Automation behavior deep link kept a fifth-level hash');
            """
        )
        for removed in (
            '"route-prompt"', '"persona-prompts"', "related_services",
        ):
            self.assertNotIn(removed, source)
        self.assertNotIn('["route-strategy", "account_tree", "Route Strategy", "RT"]', source)
        self.assertNotIn("renderRouteStrategy", source)
        self.assertNotIn("Route execution", source)
        self.assertNotIn("inspect-route", source)
        index = Path("ui/workspace-ui/admin/index.html").read_text(encoding="utf-8")
        self.assertIn("20260731-agent-only-navigation-1", index)
        for marker in (
            "/api/workspace/admin/prompts/",
            "data-prompt-draft-form",
            "schedule-prompt-version",
            "unschedule-prompt-version",
            "restore-prompt-version",
            "toggle-prompt-diff",
            "next successful daily deployment",
        ):
            self.assertIn(marker, source)

        core_load = source[source.index("async function loadAdminData"):source.index("function signOut")]
        promise_all_start = core_load.index("Promise.all")
        promise_all_end = core_load.index("]);", promise_all_start)
        self.assertNotIn(
            "/api/workspace/admin/environment-config",
            core_load[promise_all_start:promise_all_end],
        )
        self.assertNotIn(
            "/api/workspace/admin/agent-config",
            core_load[promise_all_start:promise_all_end],
        )
        self.assertNotIn(
            "/api/workspace/admin/account-routing/config",
            core_load[promise_all_start:promise_all_end],
        )

    def test_agent_config_lazy_load_has_local_success_and_error_states(self) -> None:
        self.run_admin_app_script(
            """
            accessToken = 'admin-token';
            currentAccount = { account_id: 'admin', role: 'admin' };
            adminSection = 'agent-config';
            const requestedUrls = [];
            fetch = async (url) => {
              requestedUrls.push(String(url));
              return { ok: true, status: 200, json: async () => ({ agents: [], route_navigation: null, route_runtime: {}, automation_personas: [] }) };
            };
            await loadAgentConfig();
            if (requestedUrls.length !== 1 || requestedUrls[0] !== '/api/workspace/admin/agent-config') throw new Error('agent config was not loaded independently');
            if (!agentConfigData || agentConfigLoading || agentConfigLoadError) throw new Error('agent config success state is invalid');
            agentConfigData = null;
            fetch = async () => ({ ok: false, status: 503, json: async () => ({ detail: 'catalog unavailable' }) });
            await loadAgentConfig({ force: true });
            if (agentConfigLoadError !== 'catalog unavailable') throw new Error('agent config error was not scoped locally');
            if (!renderAgentConfig().includes('Retry')) throw new Error('agent config retry state missing');
            """
        )

    def test_automation_router_persona_selector_uses_seed_order_style_and_accessible_selection(self) -> None:
        self.run_admin_app_script(
            """
            agentConfigData = { automation_personas: [
              { persona_key: 'custom-calm', display_name: 'Custom Calm', enabled: true, published_version: 4, versions: [{ version: 4, status: 'published', content: { instruction: 'Calm custom voice', signature: 'Best,\\nSid' } }] },
              { persona_key: 'default-support', display_name: 'Sid Warm', enabled: true, published_version: 3, versions: [{ version: 3, status: 'published', content: { instruction: 'Warm voice', signature: 'Best,\\nSid' } }] },
              { persona_key: 'sid-bright', display_name: 'Sid Bright', enabled: false, published_version: 2, versions: [{ version: 2, status: 'published', content: { instruction: 'Bright voice', signature: 'Best,\\nSid' } }] },
              { persona_key: 'sid-precise', display_name: 'Sid Precise', enabled: true, published_version: 5, versions: [{ version: 5, status: 'published', content: { instruction: 'Precise voice', signature: 'Best,\\nSid' } }] },
            ] };
            selectedPersonaKey = '';
            const markup = renderAutomationPersonaPanel();
            const preciseIndex = markup.indexOf('data-persona-key="sid-precise"');
            const brightIndex = markup.indexOf('data-persona-key="sid-bright"');
            const warmIndex = markup.indexOf('data-persona-key="default-support"');
            const customIndex = markup.indexOf('data-persona-key="custom-calm"');
            if (!(preciseIndex < brightIndex && brightIndex < warmIndex && warmIndex < customIndex)) throw new Error('seed Persona order is not stable');
            const preciseButton = markup.slice(preciseIndex, markup.indexOf('</button>', preciseIndex));
            const brightButton = markup.slice(brightIndex, markup.indexOf('</button>', brightIndex));
            const warmButton = markup.slice(warmIndex, markup.indexOf('</button>', warmIndex));
            const customButton = markup.slice(customIndex, markup.indexOf('</button>', customIndex));
            if (!preciseButton.includes('Precise') || !preciseButton.includes('Enabled') || !preciseButton.includes('Published v5') || !preciseButton.includes('aria-pressed="true"')) throw new Error('Precise selector contract missing');
            if (!brightButton.includes('Bright') || !brightButton.includes('Disabled') || !brightButton.includes('Published v2') || !brightButton.includes('aria-pressed="false"')) throw new Error('Bright selector contract missing');
            if (!warmButton.includes('Warm') || !warmButton.includes('Enabled') || !warmButton.includes('Published v3')) throw new Error('Warm selector contract missing');
            if (customButton.includes('Precise') || customButton.includes('Bright') || customButton.includes('Warm')) throw new Error('custom Persona received a seed style');
            if (!markup.includes('randomly selects') || !markup.includes('enabled Personas with a published version') || !markup.includes('pins that exact Persona version')) throw new Error('Persona selection and pinning explanation missing');
            if (!markup.includes('Full reruns clear') || !markup.includes('Reply-only recovery keeps') || !markup.includes('Human Review')) throw new Error('Persona reset and Human Review explanation missing');
            if (!markup.includes('name="instruction"') || !markup.includes('name="signature"')) throw new Error('Instruction and Signature must remain independent fields');
            if (!markup.includes('aria-label="Create Persona"')) throw new Error('Create Persona capability was removed');

            personaDraftValues = {
              'sid-precise': { instruction: 'Precise operator draft', signature: 'Precise signature' },
              'sid-bright': { instruction: 'Bright operator draft', signature: 'Bright signature' },
            };
            selectedPersonaKey = 'sid-precise';
            const preciseDraft = renderAutomationPersonaPanel();
            selectedPersonaKey = 'sid-bright';
            const brightDraft = renderAutomationPersonaPanel();
            if (!preciseDraft.includes('Precise operator draft') || preciseDraft.includes('Bright operator draft')) throw new Error('Precise draft leaked across Persona keys');
            if (!brightDraft.includes('Bright operator draft') || brightDraft.includes('Precise operator draft')) throw new Error('Bright draft leaked across Persona keys');
            """
        )

        styles = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")
        self.assertIn(".admin-persona-style", styles)
        self.assertIn(".admin-persona-list button:focus-visible", styles)

    def test_automation_router_persona_actions_use_persona_lifecycle_api(self) -> None:
        self.run_admin_app_script(
            """
            accessToken = 'admin-token';
            currentAccount = { account_id: 'admin', role: 'admin' };
            adminSection = 'agent-config';
            renderAdmin = () => {};
            agentConfigData = { agents: [], route_navigation: null, route_runtime: {}, automation_personas: [] };
            FormData = function FormData(form) { return { get(name) { return form.values[name]; }, entries() { return []; } }; };
            const requests = [];
            fetch = async (url, options = {}) => {
              requests.push({ url: String(url), options });
              if (String(url) === '/api/workspace/admin/agent-config') {
                return { ok: true, status: 200, json: async () => agentConfigData };
              }
              if (String(url).includes('/drafts')) {
                return { ok: true, status: 200, json: async () => ({ version: { version: 2, status: 'draft' } }) };
              }
              if (String(url).includes('/publish')) {
                return { ok: true, status: 200, json: async () => ({ version: { version: 2, status: 'published' } }) };
              }
              return { ok: true, status: 200, json: async () => ({ persona: { enabled: false } }) };
            };
            const form = { dataset: { personaKey: 'sid-bright' }, values: {
              instruction: 'Warm and direct', opener: 'Hello', signature: 'Best,\\nSid\\nSupport Engineer 2', change_note: 'Refine voice', based_on_version: '1'
            } };
            await createPersonaDraft(form);
            const draftRequest = requests.find(item => item.url.includes('/drafts'));
            if (!draftRequest.url.endsWith('/account-personas/sid-bright/drafts')) throw new Error('Persona draft used the wrong key');
            const draftBody = JSON.parse(draftRequest.options.body);
            if (draftBody.content.instruction !== 'Warm and direct' || draftBody.content.opener !== 'Hello' || draftBody.content.signature !== 'Best,\\nSid\\nSupport Engineer 2') throw new Error('Persona draft did not preserve unified voice fields');
            if (draftBody.based_on_version !== 1 || draftBody.change_note !== 'Refine voice') throw new Error('Persona draft version contract invalid');
            await runPersonaVersionAction('publish', 'sid-precise', 2);
            if (!requests.some(item => item.url.endsWith('/account-personas/sid-precise/versions/2/publish'))) throw new Error('Persona publish used the wrong key');
            await setPersonaEnabled('custom-calm', false);
            const toggleRequest = requests.find(item => item.url.endsWith('/account-personas/custom-calm'));
            if (toggleRequest.options.method !== 'PATCH' || JSON.parse(toggleRequest.options.body).enabled !== false) throw new Error('Persona enabled PATCH invalid');
            if (requests.filter(item => item.url === '/api/workspace/admin/agent-config').length < 3) throw new Error('Persona operations did not refresh Agent Config');
            """
        )

    def test_managed_prompt_diff_restore_and_failed_draft_preserve_operator_context(self) -> None:
        self.run_admin_app_script(
            """
            accessToken = 'admin-token';
            currentAccount = { account_id: 'admin', role: 'admin' };
            adminSection = 'agent-config';
            const managedPrompt = {
              key: 'route-system', name: 'Route classifier', version: '1', component_key: 'route-classifier', content: 'header\\nold rule\\nfooter',
              metadata: { managed: true, active_version: 1, scheduled_version: null, versions: [
                { prompt_key: 'route-system', version: 2, status: 'draft', content: 'header\\nnew rule\\nfooter', change_note: 'Change rule', created_at: '2026-07-23T00:00:00Z' },
                { prompt_key: 'route-system', version: 1, status: 'active', content: 'header\\nold rule\\nfooter', change_note: 'Initial', created_at: '2026-07-22T00:00:00Z' },
              ] }
            };
            agentConfigData = {
              agents: [{ key: 'route-agent', kind: 'agent', name: 'Route Agent', description: 'Routes.', status: 'active', components: [], prompts: [managedPrompt], skills: [], mcp_servers: [] }],
              route_navigation: { key: 'route-agent', kind: 'agent', is_agent: true, name: 'Route Agent', description: 'Routes.', status: 'active', prompt_keys: ['route-system'], capabilities: [], children: [] },
              route_runtime: { router_prompt_version: 'v1', stage_details: [] }, automation_personas: []
            };
            selectedAgentPath = ['route-agent'];
            selectedAgentViews['route-agent'] = 'prompts';
            selectedPromptVersions['route-system'] = 2;
            promptDiffKeys.add('route-system');
            const diffMarkup = renderAgentConfig();
            if (!diffMarkup.includes('is-removed') || !diffMarkup.includes('is-added')) throw new Error('line diff highlighting missing');
            if (!diffMarkup.includes('old rule') || !diffMarkup.includes('new rule')) throw new Error('diff content missing');

            FormData = function FormData(form) { return { get(name) { return form.values[name]; }, entries() { return []; } }; };
            promptEditorKeys.add('route-system');
            const form = { dataset: { promptKey: 'route-system', basedOnVersion: '1' }, values: { content: 'operator text', change_note: 'operator note' } };
            fetch = async () => ({ ok: false, status: 409, json: async () => ({ detail: 'active prompt version changed' }) });
            await createPromptDraft(form);
            if (promptDraftValues['route-system'].content !== 'operator text' || promptDraftValues['route-system'].change_note !== 'operator note') throw new Error('failed draft lost operator input');
            if (!promptEditorKeys.has('route-system') || promptOperationNotice['route-system'].tone !== 'error') throw new Error('failed draft state missing');

            let fetchCount = 0;
            fetch = async (url) => {
              fetchCount += 1;
              if (String(url).includes('/restore')) return { ok: true, status: 200, json: async () => ({ version: { version: 3, status: 'draft' } }) };
              return { ok: true, status: 200, json: async () => agentConfigData };
            };
            await runPromptVersionAction('restore', 'route-system', 1);
            if (selectedPromptVersions['route-system'] !== 3) throw new Error('restore did not select new draft');
            if (fetchCount < 2) throw new Error('restore did not refresh agent config');
            """
        )

    def test_automated_cases_defaults_to_automated_route_filter(self) -> None:
        self.run_admin_app_script(
            """
            const requestedUrls = [];
            fetch = async (url) => {
              requestedUrls.push(String(url));
              return { ok: true, status: 200, json: async () => ({ cases: [] }) };
            };
            accessToken = 'admin-token';
            currentAccount = { account_id: 'admin', role: 'admin' };
            await loadAdminData();
            if (!requestedUrls.includes('/api/workspace/admin/account-automation?route_status=automated')) {
              throw new Error('default automated cases request is not filtered');
            }
            const defaultMarkup = renderAutomatedCases();
            if (!defaultMarkup.includes('<option value="automated" selected>Automated</option>')) {
              throw new Error('Automated option is not selected by default');
            }
            automationRouteStatus = '';
            const allRoutesMarkup = renderAutomatedCases();
            if (!allRoutesMarkup.includes('<option value="" selected>All routes</option>')) {
              throw new Error('explicit All routes selection is not preserved');
            }
            """
        )

    def test_admin_session_is_role_gated_and_preserves_engineer_storage(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        for marker in (
            "supportportal_admin_workspace_access_token",
            "supportportal_admin_workspace_account",
            "supportportal_admin_workspace_account_id",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("supportportal_engineer_workspace_", source)
        self.assertNotIn('"supportportal_workspace_access_token"', source)

        self.run_admin_app_script(
            """
            accessToken = "engineer-token";
            currentAccount = { account_id: "Zac", display_name: "Zac", role: "engineer" };
            if (isAdminAuthenticated()) {
              throw new Error("admin accepted an engineer session");
            }
            renderAdmin();
            if (!root.innerHTML.includes("admin-login-page")) {
              throw new Error("admin did not show its login page for an engineer session");
            }

            accessToken = "admin-token";
            currentAccount = { account_id: "Admin", display_name: "Admin", role: "admin" };
            if (!isAdminAuthenticated()) {
              throw new Error("admin rejected a valid admin session");
            }

            localStorage.setItem(WORKSPACE_ACCESS_TOKEN_KEY, JSON.stringify("admin-token"));
            localStorage.setItem(WORKSPACE_ACCOUNT_KEY, JSON.stringify({
              account_id: "Admin", display_name: "Admin", role: "admin"
            }));
            localStorage.setItem("supportportal_engineer_workspace_access_token", JSON.stringify("engineer-token"));
            signOut({ render: false });
            if (localStorage.getItem(WORKSPACE_ACCESS_TOKEN_KEY) !== null) {
              throw new Error("admin logout did not clear the admin session");
            }
            if (localStorage.getItem("supportportal_engineer_workspace_access_token") === null) {
              throw new Error("admin logout cleared the engineer session");
            }
            """
        )

    def test_admin_status_mapping_uses_assignment_status_not_client_status_or_assignee(self) -> None:
        self.run_admin_app_script(
            """
            const pending = normalizeAdminTicket({
              engineer_case_id: "CASE-1", status: "resolved", client_status: "investigating", assignment_status: "pending",
              assigned_engineer_id: "legacy-value"
            });
            const assigned = normalizeAdminTicket({
              engineer_case_id: "CASE-2", status: "open", assignment_status: "assigned",
              assigned_engineer_id: "Maya"
            });
            const resolved = normalizeAdminTicket({
              engineer_case_id: "CASE-3", status: "communicating", assignment_status: "resolved"
            });
            if (pending.assignmentStatus !== "pending" || assigned.assignmentStatus !== "assigned" || resolved.assignmentStatus !== "resolved") {
              throw new Error("assignment status is not independent from client status and assignee");
            }
            if (pending.clientStatus !== "investigating") {
              throw new Error("admin used legacy Engineer Case status instead of Client Ticket status");
            }
            """
        )

    def test_admin_case_tabs_use_assignment_status_and_exact_columns(self) -> None:
        self.run_admin_app_script(
            """
            adminTickets = [
              normalizeAdminTicket({ engineer_case_id: "CASE-P", title: "Pending subject", client_status: "open", assignment_status: "pending", requester: "Pat" }),
              normalizeAdminTicket({ engineer_case_id: "CASE-A", title: "Assigned subject", client_status: "investigating", assignment_status: "assigned", requester: "Ari", assigned_engineer_id: "Maya" }),
              normalizeAdminTicket({ engineer_case_id: "CASE-R", title: "Resolved subject", client_status: "resolved", assignment_status: "resolved", requester: "Ren", assigned_engineer_id: "Leo" }),
            ];

            const pendingHtml = renderAdminTicketBoard("pending-assignment");
            const pendingHead = pendingHtml.match(/<thead><tr>(.*?)<\\/tr><\\/thead>/s)?.[1] || "";
            if (!pendingHtml.includes("CASE-P") || pendingHtml.includes("CASE-A") || pendingHtml.includes("CASE-R")) {
              throw new Error("Pending Assignment tab did not filter by assignment_status");
            }
            if (pendingHead !== "<th>ID</th><th>Subject</th><th>Status</th><th>Requester</th><th>Priority</th>") {
              throw new Error(`Pending Assignment columns are incorrect: ${pendingHead}`);
            }

            for (const [section, expectedId, unexpectedId] of [["assigned", "CASE-A", "CASE-R"], ["resolved", "CASE-R", "CASE-A"]]) {
              const html = renderAdminTicketBoard(section);
              const head = html.match(/<thead><tr>(.*?)<\\/tr><\\/thead>/s)?.[1] || "";
              if (!html.includes(expectedId) || html.includes(unexpectedId) || html.includes("CASE-P")) {
                throw new Error(`${section} tab did not filter by assignment_status`);
              }
              if (head !== "<th>ID</th><th>Subject</th><th>Status</th><th>Requester</th><th>Priority</th><th>Assignee</th>") {
                throw new Error(`${section} columns are incorrect: ${head}`);
              }
            }
            """
        )

        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        self.assertNotIn("data-assignment-form", source)
        self.assertNotIn("Admin adjustment", source)

    def test_workspace_admin_assets_are_self_contained(self) -> None:
        html = Path("ui/workspace-ui/admin/index.html").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")
        self.assertIn('id="workspace-admin-root"', html)
        self.assertIn("./styles.css", html)
        self.assertIn("./app.js", html)
        self.assertIn(".admin-shell", css)
        self.assertIn(".admin-case-tabs", css)
        self.assertIn(".admin-case-table", css)
        self.assertIn(".admin-case-table th {\n  padding-block: 12px;", css)

    def test_workspace_admin_login_uses_transactional_entry_contract(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        html = Path("ui/workspace-ui/admin/index.html").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")

        for marker in (
            '<strong>Admin</strong>',
            "Welcome Back",
            "An administrative workspace for managing engineer access, schedules, assignments, and SLA health.",
            "admin-login-card",
            "Secure Admin Workspace",
            '<span>Email</span>',
            'name="email"',
            'name="password"',
        ):
            self.assertIn(marker, source)
        self.assertNotIn("Account ID", source)
        self.assertIn("20260731-agent-only-navigation-1", html)
        self.assertIn(".admin-login-header", css)
        self.assertIn(".admin-login-footer", css)
        self.assertIn("@media (max-width: 640px)", css)

    def test_admin_shell_uses_collapsed_rail_logout_and_shared_tab_topbar(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        html = Path("ui/workspace-ui/admin/index.html").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")

        self.assertIn("admin-rail-footer", source)
        self.assertIn("admin-logout-btn", source)
        self.assertIn('<span class="admin-rail-label">Logout</span>', source)
        self.assertIn("ADMIN_SECTION_TITLES", source)
        self.assertIn("admin-workspace-topbar", source)
        self.assertIn("admin-account-chip", source)
        self.assertIn("admin-account-avatar", source)
        self.assertIn("admin-account-meta", source)
        self.assertNotIn("admin-user-chip", source)
        self.assertNotIn("admin-topbar-btn", source)
        self.assertIn("grid-template-columns: 96px minmax(0, 1fr)", css)
        self.assertIn("width: 264px", css)
        self.assertIn('class="admin-rail-fallback">AD</span>', source)
        self.assertIn('class="admin-rail-fallback">LO</span>', source)
        self.assertIn("material-symbols-failed", html)
        self.assertIn("material-symbols-ready", html)
        self.assertIn("document.fonts.check(fontSpec, railGlyphs)", html)
        self.assertIn('html:not(.material-symbols-ready) .admin-sidebar .admin-rail-glyph', css)
        self.assertIn('html.material-symbols-ready .admin-sidebar .admin-rail-fallback', css)
        self.assertIn(".admin-account-meta strong", css)
        self.assertIn("text-overflow: ellipsis", css)
        self.assertIn(".admin-workspace-topbar h1", css)
        self.assertIn("white-space: nowrap", css)
        self.assertNotIn("admin-account-bar", source)
        self.assertEqual(source.count("<h1"), 2)
        topbar_css = css[css.index(".admin-workspace-topbar {"):css.index(".admin-account-chip {")]
        self.assertNotIn("position: fixed", topbar_css)
        self.assertIn("max-width: none", topbar_css)
        self.assertIn("syncAdminRailScrollPosition", source)
        self.assertNotIn('scrollIntoView({ block: "nearest", inline: "center" })', source)

        self.run_admin_app_script(
            """
            currentAccount = { account_id: 'admin@example.com', display_name: 'Admin Operator', role: 'admin' };
            const titles = {
              overview: 'Operations Overview',
              'automated-cases': 'Automated Cases',
              'agent-config': 'Agent Config',
              'environment-config': 'Environment Config',
              engineers: 'Engineer Management',
              schedule: 'Weekly Schedule',
              'new-account': 'Invite a workspace member',
              'pending-assignment': 'Pending Assignment',
              assigned: 'Assigned',
              resolved: 'Resolved',
              audit: 'Audit',
            };
            for (const [section, title] of Object.entries(titles)) {
              adminSection = section;
              const sectionShell = renderAdminShell('<section data-test-tab>Tab content</section>');
              const topbar = sectionShell.slice(sectionShell.indexOf('<header class="admin-workspace-topbar">'), sectionShell.indexOf('</header>') + 9);
              if (!topbar.includes(`<h1 title="${title}">${title}</h1>`)) throw new Error(`${section} topbar title missing`);
              if (!topbar.includes('admin-account-chip') || !topbar.includes('Admin Operator')) throw new Error(`${section} topbar account missing`);
              if (sectionShell.indexOf('admin-workspace-topbar') < sectionShell.indexOf('</aside>')) throw new Error(`${section} topbar overlaps rail structure`);
              if (sectionShell.indexOf('admin-workspace-topbar') > sectionShell.indexOf('data-test-tab')) throw new Error(`${section} topbar does not precede tab content`);
            }
            adminSection = 'overview';
            const shell = renderAdminShell('<section data-test-tab>Tab content</section>');
            const footer = shell.slice(shell.indexOf('<footer class="admin-rail-footer">'), shell.indexOf('</footer>') + 9);
            if (!footer.includes('admin-logout-btn') || footer.includes('admin-account-chip')) throw new Error("rail footer is not logout-only");
            if (!shell.includes('admin-account-chip') || !shell.includes('Admin Operator') || !shell.includes('Administrator')) throw new Error("topbar account identity missing");

            const sidebarBody = { scrollLeft: 73, clientWidth: 68, scrollWidth: 236 };
            const activeLink = { offsetLeft: 104, offsetWidth: 44 };
            root.querySelector = (selector) => selector === ".admin-sidebar-body" ? sidebarBody : activeLink;
            globalThis.matchMedia = () => ({ matches: false });
            syncAdminRailScrollPosition();
            if (sidebarBody.scrollLeft !== 0) throw new Error("desktop rail retained a hidden horizontal offset");

            sidebarBody.clientWidth = 300;
            sidebarBody.scrollWidth = 720;
            activeLink.offsetLeft = 430;
            activeLink.offsetWidth = 120;
            globalThis.matchMedia = () => ({ matches: true });
            syncAdminRailScrollPosition();
            if (sidebarBody.scrollLeft !== 340) throw new Error("mobile active navigation was not centered within its own scroller");
            """
        )

    def test_admin_account_entry_uses_simple_blue_button_and_plain_back_link(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")

        self.assertIn('class="btn btn-primary admin-new-account-btn"', source)
        self.assertIn('aria-hidden="true">add</span><span>New Account</span>', source)
        self.assertIn(".admin-new-account-btn", css)
        self.assertIn("background: var(--primary)", css)
        self.assertIn(".admin-back-link:visited", css)
        self.assertIn("text-decoration: none", css)

    def test_admin_invitation_submit_has_immediate_feedback_and_deduplicates_requests(self) -> None:
        self.run_admin_app_script(
            """
            let requestCount = 0;
            let resolveRequest;
            fetchJson = () => {
              requestCount += 1;
              return new Promise((resolve) => { resolveRequest = resolve; });
            };
            const attributes = new Map();
            const submit = { disabled: false, innerHTML: "original markup" };
            const errorNode = { textContent: "old error" };
            const form = {
              dataset: {},
              querySelector(selector) { return selector.includes("submit") ? submit : errorNode; },
              setAttribute(name, value) { attributes.set(name, value); },
              removeAttribute(name) { attributes.delete(name); },
            };
            const pending = handleInvitation(form);
            if (!submit.disabled || !submit.innerHTML.includes("Sending invitation...") || form.dataset.submitting !== "true") {
              throw new Error("first click did not enter the sending state immediately");
            }
            await handleInvitation(form);
            if (requestCount !== 1) throw new Error("duplicate click created another invitation request");
            resolveRequest({ invitation: { email: "test@example.com", expires_at: "2026-07-20T00:00:00Z" } });
            await pending;
            if (requestCount !== 1) throw new Error("invitation request count changed after completion");
            """
        )

    def test_admin_invitation_submit_restores_retry_state_after_failure(self) -> None:
        self.run_admin_app_script(
            """
            fetchJson = async () => { throw new Error("mail service unavailable"); };
            const attributes = new Map();
            const submit = { disabled: false, innerHTML: "original markup" };
            const errorNode = { textContent: "" };
            const form = {
              dataset: {},
              querySelector(selector) { return selector.includes("submit") ? submit : errorNode; },
              setAttribute(name, value) { attributes.set(name, value); },
              removeAttribute(name) { attributes.delete(name); },
            };
            await handleInvitation(form);
            if (submit.disabled || submit.innerHTML !== "original markup" || form.dataset.submitting || attributes.has("aria-busy")) {
              throw new Error("failed invitation did not restore the retry state");
            }
            if (errorNode.textContent !== "mail service unavailable") throw new Error("invitation error was not displayed");
            """
        )

    def test_admin_weekly_schedule_uses_blue_half_hour_name_slots(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")

        for marker in (
            "timeStringToMinutes",
            "buildScheduleSegments",
            "assignScheduleLanes",
            "buildScheduleSlots",
            "renderWeeklyTimeGrid",
            "admin-week-grid",
            "admin-week-time-column",
            "admin-week-slot",
            "repeat(48, 26px)",
            "#cae6ff",
            "#00344e",
        ):
            self.assertIn(marker, source + css)
        self.assertNotIn("admin-week-shift", source + css)
        self.assertNotIn("repeat(96, 12px)", css)
        self.assertNotIn("admin-roster-table", source + css)
        self.assertNotIn("#16262d", css)
        self.assertNotIn("#dff3f5", css)
        self.assertIn("width: max-content", css)
        self.assertIn("max-width: calc((100% / var(--lane-count)) - 8px)", css)
        self.assertIn("border-radius: 999px", css)
        self.assertNotIn("\n  width: calc((100% / var(--lane-count)) - 8px)", css)

    def test_admin_schedule_is_a_separate_tab_with_engineer_edit_entry(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")

        for marker in (
            '["schedule", "calendar_month", "Schedule", "SC"]',
            'adminSection === "schedule"',
            "renderAdminSchedule()",
            "Engineer Schedules",
            "admin-roster-statuses",
            'adminSection = "schedule"',
            'globalThis.location.hash = "schedule"',
        ):
            self.assertIn(marker, source)

        self.run_admin_app_script(
            """
            scheduleData = { timezone: "Asia/Shanghai", engineers: [
              { account_id: "zac", email: "zac@example.com", display_name: "Zac", is_on_schedule_now: false, shifts: [] },
            ] };
            const management = renderAdminEngineerManagement();
            const schedule = renderAdminSchedule();
            if (management.includes("admin-week-grid")) throw new Error("weekly grid remained in Engineer Management");
            if (!management.includes("Engineer Schedules") || !management.includes("off schedule") || !management.includes("Modify Zac schedule")) {
              throw new Error("Engineer Management is missing schedule management access");
            }
            if (!schedule.includes("admin-week-grid") || !schedule.includes("Schedule Grid")) {
              throw new Error("Schedule tab is missing the weekly grid");
            }
            selectedEngineerId = "zac";
            if (!renderAdminSchedule().includes('aria-label="Modify shifts"')) {
              throw new Error("Schedule tab did not open the selected engineer editor");
            }
            """
        )

    def test_admin_time_labels_share_the_first_half_hour_slot_center(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")

        self.assertIn('class="admin-week-time" data-hour="${hour}" style="grid-row:${row}"', source)
        self.assertIn('style="grid-column:${slot.weekday + 2};grid-row:${row};', source)
        self.assertIn("const row = 2 + hour * 2", source)
        self.assertIn("const row = 2 + Math.floor(slot.slotStart / 30)", source)
        self.assertIn("align-self: center", css)
        self.assertNotIn("transform: translateY(-50%)", css)

    def test_admin_schedule_editor_uses_finite_half_hour_selects(self) -> None:
        self.run_admin_app_script(
            """
            scheduleData = { timezone: "Asia/Shanghai", engineers: [] };
            const html = renderScheduleEditor({
              account_id: "zac", display_name: "Zac", shifts: [{ weekday: 0, start: "00:00", end: "24:00" }],
            }, ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]);
            if (html.includes('type="time"') || html.includes('name="availability"') || html.includes('name="reason"')) {
              throw new Error("schedule editor still contains removed controls");
            }
            if (!html.includes('name="start_hour_0"') || !html.includes('name="end_hour_0"') || !html.includes('value="24" selected')) {
              throw new Error("finite hour controls or 24:00 selection are missing");
            }
            if (!html.includes('name="start_minute_0"') || !html.includes('value="30"')) {
              throw new Error("half-hour minute controls are missing");
            }
            if (!html.includes('data-end-minute="0"') || !html.includes('disabled')) {
              throw new Error("24:00 did not lock its minute to 00");
            }
            """
        )

    def test_admin_schedule_save_is_immediate_deduplicated_and_schedule_only(self) -> None:
        self.run_admin_app_script(
            """
            let requestCount = 0;
            let requestUrl = "";
            let requestBody = null;
            let resolveRequest;
            FormData = function FormData() { return { get(name) {
              const values = { day_0: "on", start_hour_0: "09", start_minute_0: "00", end_hour_0: "17", end_minute_0: "30" };
              return values[name] || "";
            } }; };
            fetchJson = (url, options) => {
              requestCount += 1;
              requestUrl = url;
              requestBody = JSON.parse(options.body);
              return new Promise((resolve) => { resolveRequest = resolve; });
            };
            const attributes = new Map();
            const submit = { disabled: false, innerHTML: "save" };
            const errorNode = { textContent: "old" };
            const form = {
              dataset: { engineerId: "zac" },
              querySelector(selector) { return selector.includes("submit") ? submit : errorNode; },
              setAttribute(name, value) { attributes.set(name, value); },
              removeAttribute(name) { attributes.delete(name); },
            };
            const pending = handleScheduleUpdate(form);
            if (!submit.disabled || !submit.innerHTML.includes("Saving schedule...") || form.dataset.submitting !== "true") {
              throw new Error("first save did not enter loading state immediately");
            }
            await handleScheduleUpdate(form);
            if (requestCount !== 1 || !requestUrl.endsWith("/schedule") || requestUrl.includes("availability")) {
              throw new Error("save did not issue exactly one schedule request");
            }
            if (JSON.stringify(requestBody.shifts) !== JSON.stringify([{ weekday: 0, start: "09:00", end: "17:30" }])) {
              throw new Error("save payload did not preserve half-hour values");
            }
            resolveRequest({ timezone: "Asia/Shanghai", engineers: [] });
            await pending;
            if (scheduleNotice !== "Schedule saved" || requestCount !== 1) throw new Error("save success was not retained");
            """
        )

    def test_admin_schedule_save_restores_editor_after_failure(self) -> None:
        self.run_admin_app_script(
            """
            FormData = function FormData() { return { get() { return ""; } }; };
            fetchJson = async () => { throw new Error("schedule unavailable"); };
            const attributes = new Map();
            const submit = { disabled: false, innerHTML: "save" };
            const errorNode = { textContent: "" };
            const form = {
              dataset: { engineerId: "zac" },
              querySelector(selector) { return selector.includes("submit") ? submit : errorNode; },
              setAttribute(name, value) { attributes.set(name, value); },
              removeAttribute(name) { attributes.delete(name); },
            };
            await handleScheduleUpdate(form);
            if (submit.disabled || submit.innerHTML !== "save" || form.dataset.submitting || attributes.has("aria-busy")) {
              throw new Error("failed save did not restore the editor");
            }
            if (errorNode.textContent !== "schedule unavailable") throw new Error("save error was not visible");
            """
        )

    def test_admin_uses_schedule_as_the_only_engineer_availability_state(self) -> None:
        source = Path("ui/workspace-ui/admin/app.js").read_text(encoding="utf-8")
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")

        self.assertNotIn("handleAvailabilityToggle", source)
        self.assertNotIn("toggle-availability", source)
        self.assertNotIn("availability_reason", source)
        self.assertNotIn("admin-availability-toggle", source + css)
        self.assertNotIn("availability_reassigned", source)

    def test_admin_schedule_uses_page_scroll_and_sidebar_scrollbar_is_hidden(self) -> None:
        css = Path("ui/workspace-ui/admin/styles.css").read_text(encoding="utf-8")

        self.assertIn("max-height: none", css)
        self.assertIn("overflow: visible", css)
        self.assertIn("overflow-x: auto", css)
        self.assertIn("overflow-y: visible", css)
        self.assertNotIn("height: min(70vh, 720px)", css)
        self.assertIn(".admin-sidebar-body::-webkit-scrollbar", css)
        self.assertIn("scrollbar-width: none", css)

    def test_admin_weekly_schedule_splits_overnight_and_assigns_overlap_lanes(self) -> None:
        self.run_admin_app_script(
            """
            const engineers = [
              { account_id: "zac", display_name: "Zac", shifts: [
                { weekday: 6, start: "22:00", end: "06:00" },
                { weekday: 0, start: "09:00", end: "17:00" },
              ] },
              { account_id: "maya", display_name: "Maya", shifts: [
                { weekday: 0, start: "10:00", end: "14:00" },
              ] },
            ];
            const segments = assignScheduleLanes(buildScheduleSegments(engineers));
            const sunday = segments.find((segment) => segment.weekday === 6 && segment.startMinute === 1320 && segment.endMinute === 1440);
            const mondayOvernight = segments.find((segment) => segment.weekday === 0 && segment.startMinute === 0 && segment.endMinute === 360);
            const zacMonday = segments.find((segment) => segment.weekday === 0 && segment.startMinute === 540);
            const mayaMonday = segments.find((segment) => segment.weekday === 0 && segment.startMinute === 600);
            if (!sunday || !mondayOvernight) throw new Error("Sunday overnight shift was not split across the week boundary");
            if (sunday.label !== "22:00-24:00" || mondayOvernight.label !== "00:00-06:00") {
              throw new Error("overnight shift segments do not display their actual day-local time range");
            }
            if (sunday.laneCount !== 1 || mondayOvernight.laneCount !== 1) {
              throw new Error("non-overlapping overnight segments should occupy the full day column");
            }
            if (!zacMonday || !mayaMonday || zacMonday.lane === mayaMonday.lane || zacMonday.laneCount < 2 || mayaMonday.laneCount < 2) {
              throw new Error("overlapping Monday shifts were not assigned separate lanes");
            }
            const slots = buildScheduleSlots(engineers);
            const mondayTen = slots.filter((slot) => slot.weekday === 0 && slot.slotStart === 600);
            if (mondayTen.length !== 2 || mondayTen[0].lane === mondayTen[1].lane || mondayTen.some((slot) => slot.laneCount !== 2)) {
              throw new Error("overlapping engineers were not retained side by side in the same half-hour slot");
            }
            const zacMondaySlots = slots.filter((slot) => slot.engineer.account_id === "zac" && slot.weekday === 0 && slot.slotStart >= 540);
            if (zacMondaySlots.length !== 16) throw new Error("09:00-17:00 did not produce sixteen half-hour slots");
            const grid = renderWeeklyTimeGrid(engineers, ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]);
            if ((grid.match(/class="admin-week-slot"/g) || []).length !== slots.length) {
              throw new Error("the grid did not render one name block per schedule slot");
            }
            if (!grid.includes("<span>Zac</span>") || !grid.includes("<span>Maya</span>")) {
              throw new Error("schedule slots did not render engineer names");
            }
            """
        )

    def test_workspace_setup_page_uses_one_time_invitation_api(self) -> None:
        source = Path("ui/workspace-ui/setup/app.js").read_text(encoding="utf-8")
        html = Path("ui/workspace-ui/setup/index.html").read_text(encoding="utf-8")

        self.assertIn("/api/workspace/invitations/complete", source)
        self.assertIn("/api/workspace/invitations/${encodeURIComponent(token)}", source)
        self.assertIn('<span>Email</span>', source)
        self.assertIn('name="email"', source)
        self.assertIn('readonly aria-readonly="true"', source)
        self.assertNotIn('name="account_id"', source)
        self.assertNotIn("account_id:", source)
        self.assertIn('name="confirm_password"', source)
        self.assertIn("20260719-setup-email-identity-1", html)
        self.assertIn('id="workspace-setup-root"', html)


if __name__ == "__main__":
    unittest.main()
