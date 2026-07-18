// ============================================
// STATE MANAGEMENT
// ============================================
let activeThreadId = localStorage.getItem("active_thread_id") || null;
let savedApiKey = localStorage.getItem("groq_api_key") || "";
let latestState = null;
let inspectedState = null;
let executionTimer = null;
let executionStartTime = null;

// SVG Nodes Mapping
const NODE_IDS = {
    "START": "node-START",
    "planner": "node-planner",
    "executor": "node-executor",
    "synthesizer": "node-synthesizer",
    "critique": "node-critique",
    "human_approval": "node-human_approval",
    "done": "node-done",
    "END": "node-END"
};

// Node execution order for progress tracking
const NODE_ORDER = ["planner", "executor", "synthesizer", "critique"];

// Edge flow mapping: which edges light up when transitioning INTO a node
const EDGE_FLOW = {
    "planner":       ["edge-start-planner"],
    "executor":      ["edge-planner-executor"],
    "synthesizer":   ["edge-executor-synthesizer"],
    "critique":      ["edge-synthesizer-critique"],
    "done":          ["edge-critique-done"],
    "human_approval":["edge-critique-human"],
    "END":           ["edge-done-end"]
};

// ============================================
// INITIALIZATION
// ============================================
document.addEventListener("DOMContentLoaded", () => {
    // Load saved API key
    if (savedApiKey) {
        document.getElementById("groq-key").value = savedApiKey;
        updateStatusBadge(true);
    } else {
        updateStatusBadge(false);
    }
    
    // Wire Suggestion Tags
    document.querySelectorAll(".suggestion-tag").forEach(tag => {
        tag.addEventListener("click", () => {
            document.getElementById("agent-query").value = tag.getAttribute("data-query");
            // Visual feedback
            tag.style.borderColor = "var(--color-primary)";
            tag.style.color = "var(--text-primary)";
            setTimeout(() => {
                tag.style.borderColor = "";
                tag.style.color = "";
            }, 600);
        });
    });

    // Wire buttons
    document.getElementById("run-agent-btn").addEventListener("click", runAgent);
    document.getElementById("save-key-btn").addEventListener("click", saveApiKey);
    document.getElementById("btn-approve").addEventListener("click", () => submitHumanApproval(true));
    document.getElementById("btn-reject").addEventListener("click", () => submitHumanApproval(false));

    // Wire Clear Console
    document.getElementById("clear-console-btn").addEventListener("click", () => {
        const consoleLogs = document.getElementById("console-logs");
        consoleLogs.innerHTML = "";
        addConsoleLine("System", "system", "Console cleared.");
    });

    // Wire Tab Switchers
    document.querySelectorAll(".tab-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
            document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
            btn.classList.add("active");
            document.getElementById(btn.getAttribute("data-tab")).classList.add("active");
        });
    });

    // Restore active thread
    if (activeThreadId) {
        document.getElementById("thread-display").style.display = "flex";
        document.getElementById("active-thread-id").textContent = activeThreadId.substring(0, 8) + "...";
        fetchCurrentState(activeThreadId);
    }
});

// ============================================
// HELPERS
// ============================================
function getTimestamp() {
    return new Date().toLocaleTimeString();
}

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

// Simple markdown-to-HTML renderer
function renderMarkdown(text) {
    if (!text) return "";
    let html = escapeHtml(text);
    // Bold: **text**
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    // Headers: ### text
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    // Bullet lists: - item
    html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
    html = html.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
    // Numbered lists: 1. item
    html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
    // Newlines to <br> (but not inside tags)
    html = html.replace(/\n/g, '<br>');
    // Clean up double <br> after block elements
    html = html.replace(/<\/h3><br>/g, '</h3>');
    html = html.replace(/<\/ul><br>/g, '</ul>');
    return html;
}

// ============================================
// API KEY
// ============================================
function saveApiKey() {
    const key = document.getElementById("groq-key").value.trim();
    if (key) {
        localStorage.setItem("groq_api_key", key);
        savedApiKey = key;
        updateStatusBadge(true);
        addConsoleLine("System", "success", "API Key saved. Using Groq live backend.");
    } else {
        localStorage.removeItem("groq_api_key");
        savedApiKey = "";
        updateStatusBadge(false);
        addConsoleLine("System", "warning", "API Key cleared. Falling back to Simulation Mode.");
    }
}

function updateStatusBadge(hasKey) {
    const badge = document.getElementById("backend-status");
    if (hasKey) {
        badge.textContent = "Connected to Groq";
        badge.className = "badge success-badge";
    } else {
        badge.textContent = "Simulation Mode";
        badge.className = "badge";
    }
}

// ============================================
// CONSOLE LOGGING
// ============================================
function addConsoleLine(nodeName, status, message) {
    const consoleLogs = document.getElementById("console-logs");
    const line = document.createElement("div");
    line.className = `console-line ${status}`;
    line.innerHTML = `<span class="timestamp">[${getTimestamp()}]</span> <span class="node-badge">${nodeName}</span> <span class="msg">${message}</span>`;
    consoleLogs.appendChild(line);
    consoleLogs.scrollTop = consoleLogs.scrollHeight;
}

// ============================================
// EXECUTION TIMER
// ============================================
function startTimer() {
    executionStartTime = Date.now();
    const timerEl = document.getElementById("progress-timer");
    executionTimer = setInterval(() => {
        const elapsed = ((Date.now() - executionStartTime) / 1000).toFixed(1);
        timerEl.textContent = elapsed + "s";
    }, 100);
}

function stopTimer() {
    if (executionTimer) {
        clearInterval(executionTimer);
        executionTimer = null;
    }
}

function showProgress(label, percent) {
    const container = document.getElementById("progress-container");
    container.style.display = "flex";
    document.getElementById("progress-label").textContent = label;
    const fill = document.getElementById("progress-fill");
    fill.style.width = percent + "%";
    if (percent < 100) {
        fill.classList.add("animating");
    } else {
        fill.classList.remove("animating");
    }
}

function hideProgress() {
    document.getElementById("progress-container").style.display = "none";
    document.getElementById("progress-fill").style.width = "0%";
    document.getElementById("progress-fill").classList.remove("animating");
}

// ============================================
// MAIN: RUN AGENT
// ============================================
async function runAgent() {
    const query = document.getElementById("agent-query").value.trim();
    if (!query) {
        addConsoleLine("System", "warning", "Please enter a query/prompt first.");
        return;
    }

    const runBtn = document.getElementById("run-agent-btn");
    runBtn.disabled = true;
    runBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Running Graph...';

    // Clear UI
    clearVisualGraph();
    document.getElementById("human-interaction-box").style.display = "none";
    document.getElementById("console-logs").innerHTML = "";
    addConsoleLine("System", "system", "Initiating LangGraph execution...");

    // Start timer and progress
    showProgress("Starting graph...", 5);
    startTimer();

    const newThreadId = uuidv4();
    activeThreadId = newThreadId;
    localStorage.setItem("active_thread_id", activeThreadId);
    
    document.getElementById("thread-display").style.display = "flex";
    document.getElementById("active-thread-id").textContent = activeThreadId.substring(0, 8) + "...";

    try {
        showProgress("Invoking planner → executor → critique pipeline...", 30);

        const response = await fetch("/api/run", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                question: query,
                api_key: savedApiKey || null,
                thread_id: activeThreadId
            })
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "Server error running graph");
        }

        const data = await response.json();
        latestState = data.state;
        inspectedState = data.state;

        if (data.is_paused) {
            showProgress("Paused — awaiting human decision", 75);
        } else {
            showProgress("Completed!", 100);
        }

        updateUI(data);
    } catch (error) {
        addConsoleLine("Error", "danger", error.message);
        console.error(error);
        hideProgress();
    } finally {
        stopTimer();
        runBtn.disabled = false;
        runBtn.innerHTML = '<i class="fa-solid fa-play"></i> Run LangGraph';
        
        // Auto-hide progress after a delay if completed
        if (!document.getElementById("human-interaction-box").style.display || 
            document.getElementById("human-interaction-box").style.display === "none") {
            setTimeout(hideProgress, 3000);
        }
    }
}

// ============================================
// HUMAN APPROVAL / REJECTION
// ============================================
async function submitHumanApproval(approved) {
    const feedback = document.getElementById("human-feedback").value.trim();
    if (!approved && !feedback) {
        addConsoleLine("System", "warning", "Please provide instructions for rewrite before rejecting.");
        return;
    }

    const approveBtn = document.getElementById("btn-approve");
    const rejectBtn = document.getElementById("btn-reject");
    approveBtn.disabled = true;
    rejectBtn.disabled = true;
    
    addConsoleLine("Human", "system", `Decision submitted (Approved: ${approved}). Resuming graph...`);
    showProgress("Resuming graph from interrupt...", 85);
    startTimer();

    try {
        const response = await fetch("/api/resume", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                thread_id: activeThreadId,
                approved: approved,
                feedback: feedback,
                api_key: savedApiKey || null
            })
        });

        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "Failed to resume agent");
        }

        const data = await response.json();
        latestState = data.state;
        inspectedState = data.state;
        
        document.getElementById("human-feedback").value = "";
        document.getElementById("human-interaction-box").style.display = "none";
        
        if (data.is_paused) {
            showProgress("Paused again — awaiting decision", 75);
        } else {
            showProgress("Completed!", 100);
            setTimeout(hideProgress, 3000);
        }

        updateUI(data);
    } catch (error) {
        addConsoleLine("Error", "danger", error.message);
        console.error(error);
    } finally {
        stopTimer();
        approveBtn.disabled = false;
        rejectBtn.disabled = false;
    }
}

// ============================================
// FETCH STATE
// ============================================
async function fetchCurrentState(threadId) {
    try {
        const response = await fetch(`/api/state/${threadId}`);
        if (!response.ok) return;
        const data = await response.json();
        latestState = data.state;
        inspectedState = data.state;
        updateUI(data);
    } catch (error) {
        console.error("Error retrieving state:", error);
    }
}

// ============================================
// UPDATE UI
// ============================================
function updateUI(data) {
    const state = data.state;
    const nextNode = data.next_node;
    const isPaused = data.is_paused;

    // 1. Highlight visual graph
    highlightGraph(nextNode, state);

    // 2. Render logs to console
    if (state.logs) {
        const consoleLogs = document.getElementById("console-logs");
        consoleLogs.innerHTML = "";
        state.logs.forEach(log => {
            addConsoleLine(log.node, log.status, log.message);
        });
    }

    // 3. Render Inspector Tabs
    renderStateInspectors(inspectedState || state);

    // 3b. Auto-switch to Draft tab if a draft was generated
    if (state.draft) {
        document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
        document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
        const draftBtn = document.querySelector('[data-tab="tab-draft"]');
        if (draftBtn) draftBtn.classList.add("active");
        const draftTab = document.getElementById("tab-draft");
        if (draftTab) draftTab.classList.add("active");
    }

    // 4. Show human control card if paused
    if (isPaused) {
        const box = document.getElementById("human-interaction-box");
        box.style.display = "flex";
        document.getElementById("human-score").textContent = state.score.toFixed(1);
        document.getElementById("human-critique-text").textContent = state.critique;
        box.scrollIntoView({ behavior: "smooth", block: "center" });
        addConsoleLine("System", "warning", "⚠️ Graph PAUSED before 'human_approval'. Awaiting your decision...");
    } else {
        document.getElementById("human-interaction-box").style.display = "none";
    }

    // 5. Load State History
    loadHistoryList();
}

// ============================================
// STATE INSPECTORS
// ============================================
function renderStateInspectors(state) {
    if (!state) return;
    
    // Tab 1: Draft
    const draftContainer = document.getElementById("draft-container");
    if (state.draft) {
        const renderedDraft = renderMarkdown(state.draft);
        draftContainer.innerHTML = `
            <div class="draft-toolbar">
                <h3>📄 Synthesized Draft</h3>
                <div style="display:flex; align-items:center; gap:8px;">
                    <span class="copy-feedback" id="copy-feedback">Copied!</span>
                    <button class="btn btn-sm btn-outline" id="copy-draft-btn" title="Copy to clipboard">
                        <i class="fa-regular fa-copy"></i> Copy
                    </button>
                </div>
            </div>
            <div class="draft-markdown">${renderedDraft}</div>
        `;
        // Wire copy button
        document.getElementById("copy-draft-btn").addEventListener("click", () => {
            navigator.clipboard.writeText(state.draft).then(() => {
                const fb = document.getElementById("copy-feedback");
                fb.classList.add("show");
                setTimeout(() => fb.classList.remove("show"), 1500);
            });
        });
    } else {
        draftContainer.innerHTML = '<div class="empty-state"><i class="fa-regular fa-file-lines"></i> No draft generated yet. Click "Run LangGraph" to begin.</div>';
    }

    // Tab 2: Plan
    const planContainer = document.getElementById("plan-container");
    if (state.plan && state.plan.length > 0) {
        let planHtml = '<h3>🧑‍✈️ Planner Output</h3><ul class="plan-list">';
        state.plan.forEach((step, idx) => {
            let itemClass = "plan-item-ui";
            
            if (idx < state.current_step_index) {
                itemClass += " completed";
            } else if (idx === state.current_step_index && state.current_step_index < state.plan.length && !state.approved) {
                itemClass += " active";
            }
            
            planHtml += `
                <li class="${itemClass}">
                    <span class="step-num">${idx + 1}.</span>
                    <span class="step-body">${escapeHtml(step)}</span>
                </li>
            `;
        });
        planHtml += '</ul>';
        
        // Worker results
        if (state.worker_results && state.worker_results.length > 0) {
            planHtml += '<h3 style="margin-top:20px;">🔧 Worker Results</h3>';
            state.worker_results.forEach((res, idx) => {
                planHtml += `
                    <div class="worker-result-card">
                        <strong>Sub-task ${idx + 1}: ${escapeHtml(state.plan[idx] || "")}</strong>
                        <div class="result-body">${renderMarkdown(res)}</div>
                    </div>
                `;
            });
        }
        planContainer.innerHTML = planHtml;
    } else {
        planContainer.innerHTML = '<div class="empty-state"><i class="fa-regular fa-rectangle-list"></i> No plan generated yet.</div>';
    }

    // Tab 3: Critique
    const critiqueContainer = document.getElementById("critique-container");
    if (state.critique || state.score) {
        const scoreClass = state.score >= 8 ? "success-score" : "";
        critiqueContainer.innerHTML = `
            <h3>🔎 Review Verdict</h3>
            <div class="score-display-card ${scoreClass}" style="margin-bottom:14px;">
                <div class="score-num ${state.score >= 8 ? 'high' : ''}">${state.score.toFixed(1)}</div>
                <div>
                    <div class="score-label">Quality Score</div>
                    <div style="font-size:10px; color:var(--text-muted); margin-top:2px;">
                        ${state.score >= 8 ? '✅ Approved' : state.score >= 6 ? '⚠️ Borderline' : '❌ Needs rewrite'}
                    </div>
                </div>
            </div>
            <p style="font-size:12px; font-weight:600; margin-bottom:4px;">Critique Feedback:</p>
            <div class="critique-summary" style="border-color: ${state.score >= 8 ? 'var(--color-success)' : 'var(--color-warning)'}">
                ${escapeHtml(state.critique || 'No feedback details.')}
            </div>
            <div style="margin-top:14px; font-size:11px; color:var(--text-muted); display:flex; gap:16px;">
                <span>Loop tries: <strong>${state.tries || 0}</strong></span>
                <span>Approved: <strong>${state.approved ? '✅ Yes' : '❌ No'}</strong></span>
            </div>
        `;
    } else {
        critiqueContainer.innerHTML = '<div class="empty-state"><i class="fa-regular fa-star-half-stroke"></i> No critique rating available yet.</div>';
    }

    // Tab 4: Raw State JSON
    document.getElementById("raw-state-json").textContent = JSON.stringify(state, null, 2);
}

// ============================================
// HISTORY LIST
// ============================================
async function loadHistoryList() {
    if (!activeThreadId) return;

    try {
        const response = await fetch(`/api/history/${activeThreadId}`);
        if (!response.ok) return;

        const data = await response.json();
        const historyContainer = document.getElementById("history-container");
        historyContainer.innerHTML = "";

        if (!data.history || data.history.length === 0) {
            historyContainer.innerHTML = '<div class="empty-state"><i class="fa-regular fa-clock"></i> No checkpoint history yet.</div>';
            return;
        }

        data.history.forEach((hist, index) => {
            const item = document.createElement("div");
            item.className = "history-item";
            if (inspectedState && JSON.stringify(inspectedState.logs) === JSON.stringify(hist.values.logs)) {
                item.className += " active";
            }

            let nodeName = "Initial State";
            if (hist.values.logs && hist.values.logs.length > 0) {
                nodeName = hist.values.logs[hist.values.logs.length - 1].node;
            }

            const tries = hist.values.tries || 0;
            const score = hist.values.score ? hist.values.score.toFixed(1) : "—";

            item.innerHTML = `
                <div class="history-item-left">
                    <span class="history-node">${nodeName}</span>
                    <span class="history-time"><i class="fa-regular fa-clock"></i> Step ${index + 1} · Tries: ${tries}</span>
                </div>
                <span class="history-badge" style="background: ${score !== '—' ? 'rgba(168,85,247,0.15)' : 'rgba(255,255,255,0.05)'}; color: ${score !== '—' ? 'var(--color-secondary)' : 'var(--text-muted)'}">
                    ${score !== '—' ? score + '/10' : 'Init'}
                </span>
            `;

            item.addEventListener("click", () => {
                inspectedState = hist.values;
                document.querySelectorAll(".history-item").forEach(el => el.classList.remove("active"));
                item.classList.add("active");
                renderStateInspectors(inspectedState);
                addConsoleLine("Viewer", "system", `Inspecting checkpoint: ${nodeName} (Step ${index + 1})`);
            });

            historyContainer.appendChild(item);
        });
    } catch (error) {
        console.error("Error retrieving state history:", error);
    }
}

// ============================================
// GRAPH HIGHLIGHTING
// ============================================
function highlightGraph(nextNode, state) {
    clearVisualGraph();

    const logs = state.logs || [];

    // Determine the active node
    let activeNode = null;
    if (nextNode && nextNode.length > 0) {
        activeNode = nextNode[0];
    } else if (state.approved) {
        activeNode = "END";
    }

    // Build set of completed node names from logs
    const completedNodes = new Set();
    logs.forEach(log => {
        const n = log.node.toLowerCase();
        // Map log node names to our graph IDs
        if (n === "planner") completedNodes.add("planner");
        else if (n === "executor") completedNodes.add("executor");
        else if (n === "synthesizer") completedNodes.add("synthesizer");
        else if (n === "critique") completedNodes.add("critique");
        else if (n === "preparerewrite") completedNodes.add("prepare_rewrite");
        else if (n === "humanapproval") completedNodes.add("human_approval");
        else if (n === "done") completedNodes.add("done");
    });

    // Always mark START as completed if anything ran
    if (logs.length > 0) completedNodes.add("START");

    // Highlight nodes
    for (const [key, elementId] of Object.entries(NODE_IDS)) {
        const svgEl = document.getElementById(elementId);
        if (!svgEl) continue;

        if (key === activeNode) {
            svgEl.classList.add("active-step");
        } else if (completedNodes.has(key)) {
            svgEl.classList.add("completed-step");
        }
    }

    // If approved and finished, mark Done and END as completed
    if (state.approved && (!nextNode || nextNode.length === 0)) {
        const doneEl = document.getElementById(NODE_IDS["done"]);
        const endEl = document.getElementById(NODE_IDS["END"]);
        if (doneEl) doneEl.classList.add("completed-step");
        if (endEl) endEl.classList.add("completed-step");
    }

    // Light up edges leading into completed or active nodes
    for (const [nodeKey, edgeIds] of Object.entries(EDGE_FLOW)) {
        edgeIds.forEach(edgeId => {
            const edgeLine = document.getElementById(edgeId);
            if (!edgeLine) return;

            if (nodeKey === activeNode) {
                edgeLine.classList.add("active-edge");
                edgeLine.setAttribute("marker-end", "url(#arrow-glow)");
            } else if (completedNodes.has(nodeKey)) {
                edgeLine.classList.add("completed-edge");
                edgeLine.setAttribute("marker-end", "url(#arrow-success)");
            }
        });
    }
}

function clearVisualGraph() {
    for (const elementId of Object.values(NODE_IDS)) {
        const svgEl = document.getElementById(elementId);
        if (svgEl) svgEl.classList.remove("active-step", "completed-step");
    }

    document.querySelectorAll(".edge-line").forEach(edge => {
        edge.classList.remove("active-edge", "completed-edge");
        edge.setAttribute("marker-end", "url(#arrow)");
    });
}

// ============================================
// UTILITIES
// ============================================
function uuidv4() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        var r = Math.random() * 16 | 0, v = c == 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}
