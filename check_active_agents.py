"""
Antigravity Active Agent Inspector via Chrome DevTools Protocol (CDP)
=====================================================================
Connects to the running Google Antigravity desktop application and checks
how many agents/conversations are actively running and under which projects.

Usage:
    python check_active_agents.py
"""

import sys
import os

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Ensure safe UTF-8 output on Windows terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from cdp.client import AntigravityCDPClient
from cdp.subagent_inspector import get_subagents_fleet_summary, format_duration_human
from cdp.chat_lifecycle_inspector import inspect_conversation_lifecycle


def inspect_single_conversation(conversation_id: str):
    """Inspects a specific conversation and its subagent fleet directly from disk."""
    print("=" * 65)
    print("  Google Antigravity -- Subagent & Conversation Inspector")
    print("=" * 65)
    print(f"Target Conversation UUID: {conversation_id}\n")

    report = inspect_conversation_lifecycle(conversation_id)
    if not report.get("found"):
        print(f"[!] Conversation '{conversation_id}' not found in database or brain directory.")
        return

    print(f"Title:         {report.get('title')}")
    print(f"State:         {report.get('overall_state')} ({'Running' if report.get('is_running') else 'Idle / Stopped'})")
    print(f"Duration:      Active: {report.get('total_active_duration_formatted')} | Wall-clock: {report.get('wall_clock_duration_formatted')}")
    print(f"Prompts:       {report.get('total_prompts')} ({report.get('user_prompts_count')} user, {report.get('auto_resume_prompts_count')} resume)")
    print(f"Steps / Tools: {report.get('total_steps')} steps | {report.get('total_tool_calls')} tool calls")

    summary = report.get("subagents_summary") or get_subagents_fleet_summary(conversation_id)
    total_subs = summary.get("total_count", 0)
    active_subs = summary.get("active_count", 0)

    print("\n" + "=" * 65)
    print(f"SUBAGENT FLEET ORCHESTRATION: {total_subs} Spawned ({active_subs} Active)")
    print("=" * 65)

    if total_subs == 0:
        print("  (No subagents spawned by this conversation)")
        return

    print(f"  Total Fleet Tokens: {summary.get('total_tokens', 0):,} (Cache: {summary.get('total_cache_read_tokens', 0):,}, Hit: {summary.get('fleet_cache_hit_ratio', 0.0):.1%})")
    print(f"  Peak Subagent Ctx:  {summary.get('peak_context_tokens', 0):,} tokens")
    print("\n  Subagent Hierarchy:")

    def _print_tree(node, indent="    "):
        cid = node.get("conversation_id", "")
        role = node.get("role", "Subagent")
        m = node.get("metrics") or {}
        st_icon = "🟢" if node.get("is_alive") else ("🔴" if node.get("is_killed") else "⚪")
        ctx_curr = m.get("current_context_tokens", 0)
        ctx_peak = m.get("peak_context_tokens", 0)
        dur = m.get("duration_human", "0s")
        steps = m.get("step_count", 0)
        comp = m.get("compaction_count", 0)
        comp_str = f" | Compacted: {comp}x" if comp > 0 else ""

        print(f"{indent}└── {st_icon} {role} ({cid[:8]}...): {node.get('status_label')}")
        print(f"{indent}    Duration: {dur} | Steps: {steps}{comp_str} | Context: {ctx_curr:,} / {ctx_peak:,} tokens")
        print(f"{indent}    Tokens: Cache: {m.get('cache_read_tokens', 0):,} | In: {m.get('input_tokens', 0):,} | Out: {m.get('output_tokens', 0):,} (Hit: {m.get('cache_hit_ratio', 0.0):.1%})")
        msgs = node.get("messages") or []
        if msgs:
            print(f"{indent}    IPC Mailbox: {len(msgs)} message(s) exchanged")
            for msg in msgs[-3:]:
                print(f"{indent}      • [{msg.get('timestamp')[:19]}] {msg.get('title')}")

        for child in node.get("children", []):
            _print_tree(child, indent + "    ")

    for root_node in summary.get("subagents_tree", []):
        _print_tree(root_node)

    print("\n" + "=" * 65)


def inspect_running_agents():
    print("=" * 65)
    print("  Google Antigravity -- Active Agent & Project Inspector (CDP)")
    print("=" * 65)

    client = AntigravityCDPClient()
    ok, msg = client.connect()
    if not ok:
        print(f"\n[ERROR] Failed to connect to Antigravity via CDP: {msg}")
        print("Please ensure Antigravity is open.")
        return

    try:
        page = client.get_page()

        # Query the sidebar DOM for conversation rows, spinners, and parent projects
        data = page.evaluate("""() => {
            const rows = Array.from(document.querySelectorAll('[data-testid="conversation-row-sidebar"]'));
            
            return rows.map(r => {
                const titleEl = r.querySelector('span.truncate') || r;
                const title = (titleEl.innerText || titleEl.textContent || '').trim();
                const cid = r.getAttribute('data-cascade-id') || '';
                const isSelected = r.getAttribute('data-selected') === 'true';

                // Check for loading spinner or floating background dots in the row
                const spinner = r.querySelector('[data-testid="status-loading-spinner"], .animate-spin, .animate-dot-bounce, [aria-label="Loading"]');
                const isRunning = Boolean(spinner);

                // Extract right-side status/time label if present (e.g., 'now', '18m', '2h')
                const textNodes = Array.from(r.querySelectorAll('*')).filter(el => {
                    const txt = (el.innerText || el.textContent || '').trim();
                    return el.children.length === 0 && txt.length > 0;
                });
                const textValues = textNodes.map(el => (el.innerText || el.textContent || '').trim());
                const subtext = textValues.find(t => t !== title) || (isSelected ? 'active' : '');

                // Walk upwards and backwards to find parent project header
                let projectName = 'General / Default';
                let p = r.parentElement;
                while (p && p !== document.body) {
                    let sibling = p.previousElementSibling;
                    while (sibling) {
                        const sText = (sibling.innerText || sibling.textContent || '').trim();
                        if (sText) {
                            const firstLine = sText.split('\\n')[0].trim();
                            // Common project names or headers
                            if (['Antigravity CDP', 'Agrico-V2', '2 step automation', 'Antigravity Automation Win UIA', 'Antigravity Automation'].some(proj => firstLine.includes(proj))) {
                                projectName = firstLine;
                                break;
                            }
                        }
                        sibling = sibling.previousElementSibling;
                    }
                    if (projectName !== 'General / Default') break;
                    p = p.parentElement;
                }

                return {
                    title,
                    id: cid,
                    project: projectName,
                    isSelected,
                    isRunning,
                    subtext
                };
            });
        }""")

        # Group by project
        projects_map = {}
        running_agents = []

        for item in data:
            p_name = item["project"]
            if p_name not in projects_map:
                projects_map[p_name] = []
            projects_map[p_name].append(item)
            if item["isRunning"]:
                running_agents.append(item)

        print(f"\n[+] Total Conversations Scanned: {len(data)}")
        print(f"[+] Total Running Agents:        {len(running_agents)}")
        print("-" * 65)

        if running_agents:
            print("\n[*] ACTIVE / RUNNING AGENTS:")
            print("-" * 65)
            for idx, agent in enumerate(running_agents, 1):
                active_badge = " (Currently Focused View)" if agent["isSelected"] else " (Running in Background)"
                print(f" {idx}. Project:      {agent['project']}")
                print(f"    Conversation: {agent['title']}{active_badge}")
                print(f"    ID:           {agent['id']}")
                print(f"    Status:       RUNNING / GENERATING")
                sub_sum = get_subagents_fleet_summary(agent['id']) if agent.get('id') else {}
                if sub_sum.get("total_count", 0) > 0:
                    print(f"    Subagents:    {sub_sum['total_count']} spawned ({sub_sum['active_count']} active) | Peak Ctx: {sub_sum['peak_context_tokens']:,} | Cache Hit: {sub_sum['fleet_cache_hit_ratio']:.1%}")
                    for snode in sub_sum.get("subagents_tree", []):
                        sm = snode.get("metrics") or {}
                        st_icon = "🟢" if snode.get("is_alive") else ("🔴" if snode.get("is_killed") else "⚪")
                        print(f"      └── {st_icon} {snode.get('role')} ({snode.get('conversation_id', '')[:8]}...): {snode.get('status_label')} | Elapsed: {sm.get('duration_human')} | Ctx: {sm.get('current_context_tokens', 0):,} tokens")
                print()
        else:
            print("\n[i] No agents are currently running (all conversations idle/completed).\n")

        print("=" * 65)
        print("[#] ALL PROJECTS BREAKDOWN:")
        print("=" * 65)
        for p_name, convos in projects_map.items():
            running_count = sum(1 for c in convos if c["isRunning"])
            status_icon = "[ACTIVE]" if running_count > 0 else "[IDLE]  "
            print(f"\n{status_icon} Project: {p_name} ({len(convos)} conversations, {running_count} active)")
            for c in convos:
                if c["isRunning"]:
                    print(f"    >> [RUNNING]    {c['title']} ({c['id'][:8]}...)")
                else:
                    time_lbl = f" [{c['subtext']}]" if c['subtext'] else ""
                    print(f"       [IDLE]       {c['title']}{time_lbl}")

        print("\n" + "=" * 65)
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Google Antigravity Active Agent & Subagent Inspector")
    parser.add_argument("--id", "--conversation-id", dest="conversation_id", help="Directly inspect a conversation ID and its subagent tree from disk without CDP")
    parser.add_argument("pos_id", nargs="?", help="Optional conversation ID to inspect")
    args = parser.parse_args()

    target_id = args.conversation_id or args.pos_id
    if target_id:
        inspect_single_conversation(target_id)
    else:
        inspect_running_agents()
