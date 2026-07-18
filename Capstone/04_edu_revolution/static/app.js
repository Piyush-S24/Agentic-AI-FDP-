/**
 * EdU Revolution — Frontend Application Logic
 * Handles chat interactions, PDF uploads, document management,
 * markdown rendering, and UI animations.
 */

// ============================================================
// CONFIGURATION & STATE
// ============================================================
const API = {
    chat: '/api/chat',
    upload: '/api/upload',
    documents: '/api/documents',
    health: '/api/health',
    reset: '/api/chat/reset',
    initiatives: '/api/initiatives',
    prefill: '/api/prefill',
    register: '/api/register',
    proofs: '/api/proofs',
    login: '/api/login',
};

const state = {
    sessionId: `session_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    isProcessing: false,
    messages: [],
    prefill: {},   // accumulated form data gathered by the advisor across turns
    proofs: [],    // uploaded proof documents for the current nomination
    identityVerified: false,   // set after UMS verification
};

// Reference data for the nomination form (loaded from /api/initiatives)
let INITIATIVES = [];
let BENEFITS = [];
let YEARS = [];
let REQUIREMENTS = {};   // "initiative|benefit" -> compulsory metric field

// ============================================================
// DOM REFERENCES
// ============================================================
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const els = {
    chatMessages: $('#chatMessages'),
    chatInput: $('#chatInput'),
    sendBtn: $('#sendBtn'),
    welcomeScreen: $('#welcomeScreen'),
    sidebar: $('#sidebar'),
    sidebarOverlay: $('#sidebarOverlay'),
    menuToggle: $('#menuToggle'),
    sidebarNav: $('#sidebarNav'),
    welcomeCards: $('#welcomeCards'),
    toastContainer: $('#toastContainer'),
    newChatBtn: $('#newChatBtn'),
    chatStatus: $('#chatStatus'),
    // Nomination form modal
    applyBtn: $('#applyBtn'),
    applyBtnSide: $('#applyBtnSide'),
    applyModal: $('#applyModal'),
    applyClose: $('#applyClose'),
    applyCancel: $('#applyCancel'),
    applyForm: $('#applyForm'),
    applySubmit: $('#applySubmit'),
    applyFormMsg: $('#applyFormMsg'),
    prefillNote: $('#prefillNote'),
    initiativeSelect: $('#initiativeSelect'),
    initiativeHint: $('#initiativeHint'),
    proofDrop: $('#proofDrop'),
    proofInput: $('#proofInput'),
    proofChips: $('#proofChips'),
    proofHint: $('#proofHint'),
    verifyBtn: $('#verifyBtn'),
    verifyRegId: $('#verifyRegId'),
    verifySecret: $('#verifySecret'),
    verifyStatus: $('#verifyStatus'),
    verifyNudges: $('#verifyNudges'),
};

// ============================================================
// INITIALIZATION
// ============================================================
document.addEventListener('DOMContentLoaded', () => {
    initChat();
    initTopics();
    initSidebar();
    initApply();
});

function initChat() {
    // Send button
    els.sendBtn.addEventListener('click', sendMessage);

    // Enter to send (Shift+Enter for new line)
    els.chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // Auto-resize textarea
    els.chatInput.addEventListener('input', () => {
        els.chatInput.style.height = 'auto';
        els.chatInput.style.height = Math.min(els.chatInput.scrollHeight, 120) + 'px';
        els.sendBtn.disabled = !els.chatInput.value.trim();
    });

    // New chat
    els.newChatBtn.addEventListener('click', resetChat);
}

// ============================================================
// TOPIC SELECTION (guided entry, not a blank chatbot)
// ============================================================
// Each topic is one EDU Revolution benefit/initiative. Clicking a card seeds a
// focused question and drops the student straight into the relevant chat.
const TOPICS = [
    { group: 'benefit', icon: '🎓', label: 'Course Equivalence',
      desc: 'Exempt a course using approved learning',
      q: "I want Course Equivalence under EDU Revolution. What are the eligibility criteria, required documents, and the step-by-step process?" },
    { group: 'benefit', icon: '📈', label: 'Grade Upgradation',
      desc: 'Improve a previously earned grade',
      q: "How do I get Grade Upgradation under EDU Revolution? What makes me eligible and what proof do I need?" },
    { group: 'benefit', icon: '📅', label: '10% Attendance Benefit',
      desc: 'Attendance relaxation (CGPA ≥ 7.5)',
      q: "How can I get the 10% Attendance Benefit under EDU Revolution? Am I eligible and how do I apply?" },
    { group: 'benefit', icon: '🕒', label: 'Duty Leave',
      desc: '30–150 hrs for approved activities',
      q: "How do I apply for Duty Leave under EDU Revolution and how many hours can I get?" },
    { group: 'benefit', icon: '🧭', label: 'Recognition of Prior Learning',
      desc: 'Credit for prior skills & experience',
      q: "How does Recognition of Prior Learning (RPL) work under EDU Revolution, and how do I apply?" },
    { group: 'initiative', icon: '💰', label: 'Revenue Generation',
      desc: 'Turn income into academic benefits',
      q: "I earned revenue through my own work. How do I convert it into academic benefits under EDU Revolution?" },
    { group: 'initiative', icon: '🚀', label: 'Projects & Hackathons',
      desc: 'Convert projects into benefits',
      q: "I did a project/hackathon. What academic benefits can I get under EDU Revolution and how do I apply?" },
    { group: 'initiative', icon: '📜', label: 'NPTEL / Certifications',
      desc: 'Map MOOCs/certifications to a course',
      q: "I completed an NPTEL/MOOC certification. How do I map it to a course under EDU Revolution?" },
    { group: 'initiative', icon: '🏢', label: 'Internship Beyond Curriculum',
      desc: 'Claim benefits for internships',
      q: "I did an internship beyond the curriculum. What benefits can I claim under EDU Revolution and how?" },
];

function initTopics() {
    renderWelcomeCards();
    renderSidebarNav();
}

function renderWelcomeCards() {
    if (!els.welcomeCards) return;
    els.welcomeCards.innerHTML = TOPICS.map((t, i) => `
        <button class="topic-card" data-topic-index="${i}">
            <span class="topic-card-icon">${t.icon}</span>
            <span class="topic-card-body">
                <span class="topic-card-label">${escapeHtml(t.label)}</span>
                <span class="topic-card-desc">${escapeHtml(t.desc)}</span>
            </span>
        </button>
    `).join('');
    els.welcomeCards.querySelectorAll('.topic-card').forEach((btn) => {
        btn.addEventListener('click', () => startTopic(TOPICS[+btn.dataset.topicIndex].q));
    });
}

function renderSidebarNav() {
    if (!els.sidebarNav) return;
    const groupTitle = (g) => g === 'benefit' ? 'Academic benefits' : 'Initiatives';
    let html = '';
    let lastGroup = null;
    TOPICS.forEach((t, i) => {
        if (t.group !== lastGroup) {
            html += `<div class="sidebar-nav-title">${groupTitle(t.group)}</div>`;
            lastGroup = t.group;
        }
        html += `
            <button class="nav-topic" data-topic-index="${i}" title="${escapeHtml(t.desc)}">
                <span class="nav-topic-icon">${t.icon}</span>
                <span>${escapeHtml(t.label)}</span>
            </button>`;
    });
    els.sidebarNav.innerHTML = html;
    els.sidebarNav.querySelectorAll('.nav-topic').forEach((btn) => {
        btn.addEventListener('click', () => {
            startTopic(TOPICS[+btn.dataset.topicIndex].q);
            closeSidebar();
        });
    });
}

function startTopic(question) {
    const welcomeEl = document.getElementById('welcomeScreen');
    if (welcomeEl) welcomeEl.style.display = 'none';
    els.chatInput.value = question;
    els.chatInput.dispatchEvent(new Event('input'));
    sendMessage();
}

// ============================================================
// CHAT LOGIC
// ============================================================
async function sendMessage() {
    const text = els.chatInput.value.trim();
    if (!text || state.isProcessing) return;

    state.isProcessing = true;
    els.sendBtn.disabled = true;

    // Hide welcome screen
    const welcomeEl = document.getElementById('welcomeScreen');
    if (welcomeEl) {
        welcomeEl.style.display = 'none';
    }

    // Add user message
    addMessage('user', text);
    els.chatInput.value = '';
    els.chatInput.style.height = 'auto';

    // Show typing indicator
    const typingEl = showTypingIndicator();

    // Update status
    setStatus('Thinking...', true);

    try {
        const response = await fetch(API.chat, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: text,
                session_id: state.sessionId,
            }),
        });

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `Server error (${response.status})`);
        }

        const data = await response.json();

        // Remove typing indicator
        removeTypingIndicator(typingEl);

        // Add assistant message with metadata
        const msgEl = addMessage('assistant', data.response, data.metadata);

        // Agentic hand-off: the advisor may want to help the student FILE now.
        handleApplicationSignals(data.metadata, msgEl);

    } catch (error) {
        removeTypingIndicator(typingEl);
        addMessage('assistant', `❌ **Error:** ${error.message}\n\nPlease try again.`);
        showToast(error.message, 'error');
    } finally {
        state.isProcessing = false;
        els.sendBtn.disabled = false;
        setStatus('Ready to assist', false);
        els.chatInput.focus();
    }
}

function addMessage(role, content, metadata = null) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}`;

    const avatar = role === 'user' ? '👤' : '🎓';
    const renderedContent = role === 'assistant' ? renderMarkdown(content) : escapeHtml(content);

    let metaHtml = '';
    if (metadata && role === 'assistant') {
        const sources = metadata.sources_used || [];
        const iterations = metadata.iterations || 0;
        const chunks = metadata.chunks_retrieved || 0;
        const tools = metadata.tools_used || [];

        const badges = [];
        if (tools.length > 0) {
            // Show the agent's tool calls (deduped, in order) — the visible "agentic" trace.
            const seen = [];
            tools.forEach(t => { if (!seen.includes(t)) seen.push(t); });
            badges.push(`<span class="meta-badge tools">🛠️ agent: ${seen.map(escapeHtml).join(' → ')}</span>`);
        }
        if (sources.length > 0) badges.push(`<span class="meta-badge sources">📄 ${sources.length} source${sources.length > 1 ? 's' : ''}</span>`);
        if (metadata.engine === 'agent' && iterations > 0) badges.push(`<span class="meta-badge iterations">🔄 ${iterations} step${iterations > 1 ? 's' : ''}</span>`);
        else if (iterations > 0) badges.push(`<span class="meta-badge iterations">🔄 ${iterations} RAG loop${iterations > 1 ? 's' : ''}</span>`);
        if (chunks > 0) badges.push(`<span class="meta-badge">📊 ${chunks} chunks</span>`);

        if (badges.length) metaHtml = `<div class="message-meta">${badges.join('')}</div>`;
    }

    messageDiv.innerHTML = `
        <div class="message-avatar">${avatar}</div>
        <div>
            <div class="message-content">${renderedContent}</div>
            ${metaHtml}
        </div>
    `;

    els.chatMessages.appendChild(messageDiv);
    scrollToBottom();

    // Track in state
    state.messages.push({ role, content });
    return messageDiv;
}

function showTypingIndicator() {
    const typingDiv = document.createElement('div');
    typingDiv.className = 'typing-indicator';
    typingDiv.id = 'typingIndicator';
    typingDiv.innerHTML = `
        <div class="message-avatar" style="background: var(--gradient-accent); box-shadow: 0 4px 12px rgba(242, 101, 34, 0.3);">🎓</div>
        <div class="typing-bubble">
            <div class="typing-dots">
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
            </div>
            <span class="typing-text">Analyzing with RAG engine...</span>
        </div>
    `;
    els.chatMessages.appendChild(typingDiv);
    scrollToBottom();
    return typingDiv;
}

function removeTypingIndicator(el) {
    if (el && el.parentNode) {
        el.parentNode.removeChild(el);
    }
}

async function resetChat() {
    try {
        await fetch(`${API.reset}?session_id=${state.sessionId}`, { method: 'POST' });
    } catch (e) { /* ignore */ }

    state.sessionId = `session_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    state.messages = [];

    // Clear messages and re-create the welcome / topic-selection screen
    els.chatMessages.innerHTML = `
        <div class="welcome-screen" id="welcomeScreen">
            <div class="welcome-icon">🎓</div>
            <h2 class="welcome-title">What can EDURev Advisor help you with?</h2>
            <p class="welcome-subtitle">
                Pick the EDU Revolution benefit or activity you want to explore — I'll explain the eligibility, documents and process, help you clear any blockers, and when you're ready, file your nomination for you. Or just type your own question below.
            </p>
            <div class="welcome-cards" id="welcomeCards"></div>
        </div>
    `;
    els.welcomeCards = $('#welcomeCards');
    renderWelcomeCards();

    showToast('Conversation reset', 'info');
}

// ============================================================
// SIDEBAR (Mobile)
// ============================================================
function initSidebar() {
    els.menuToggle.addEventListener('click', toggleSidebar);
    els.sidebarOverlay.addEventListener('click', closeSidebar);
}

function toggleSidebar() {
    els.sidebar.classList.toggle('open');
    els.sidebarOverlay.classList.toggle('active');
}

function closeSidebar() {
    els.sidebar.classList.remove('open');
    els.sidebarOverlay.classList.remove('active');
}

// ============================================================
// MARKDOWN RENDERER (Lightweight, safe)
// ============================================================
// Convert GitHub-style tables (| a | b |  /  |---|---|  / rows) into <table> HTML.
function convertTables(src) {
    const lines = src.split('\n');
    const isRow = (l) => /^\s*\|.*\|\s*$/.test(l);
    const isSep = (l) => l.includes('-') && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
    const cells = (l) => l.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());

    const out = [];
    let i = 0;
    while (i < lines.length) {
        if (isRow(lines[i]) && i + 1 < lines.length && isSep(lines[i + 1])) {
            const head = cells(lines[i]);
            let j = i + 2;
            const body = [];
            while (j < lines.length && isRow(lines[j])) { body.push(cells(lines[j])); j++; }
            let t = '<table><thead><tr>' + head.map((c) => `<th>${c}</th>`).join('') + '</tr></thead>';
            if (body.length) {
                t += '<tbody>' + body.map((r) => '<tr>' + r.map((c) => `<td>${c}</td>`).join('') + '</tr>').join('') + '</tbody>';
            }
            t += '</table>';
            out.push(t);
            i = j;
        } else {
            out.push(lines[i]);
            i++;
        }
    }
    return out.join('\n');
}

function renderMarkdown(text) {
    if (!text) return '';

    // Process markdown BEFORE escaping, then sanitize only user-text parts
    let html = text;

    // Code blocks first (protect content inside from other transformations)
    const codeBlocks = [];
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (match, lang, code) => {
        const placeholder = `%%CODEBLOCK_${codeBlocks.length}%%`;
        codeBlocks.push(`<pre><code>${escapeHtml(code)}</code></pre>`);
        return placeholder;
    });

    // Inline code (protect from other transformations)
    const inlineCodes = [];
    html = html.replace(/`([^`]+)`/g, (match, code) => {
        const placeholder = `%%INLINE_${inlineCodes.length}%%`;
        inlineCodes.push(`<code>${escapeHtml(code)}</code>`);
        return placeholder;
    });

    // Headers
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');

    // Bold + Italic
    html = html.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/(?<![\w*])\*([^*]+?)\*(?![\w*])/g, '<em>$1</em>');

    // GitHub-style tables — convert before lists/paragraphs, then protect as placeholders
    const tables = [];
    html = convertTables(html).replace(/<table>[\s\S]*?<\/table>/g, (m) => {
        const ph = `%%TABLE_${tables.length}%%`;
        tables.push(m);
        return ph;
    });

    // Blockquotes
    html = html.replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>');

    // Checkboxes (must be before list items)
    html = html.replace(/^- \[ \] (.+)$/gm, '<li class="checklist-item"><input type="checkbox" disabled> $1</li>');
    html = html.replace(/^- \[x\] (.+)$/gm, '<li class="checklist-item"><input type="checkbox" checked disabled> $1</li>');

    // Unordered lists
    html = html.replace(/^\* (.+)$/gm, '<li>$1</li>');
    html = html.replace(/^- (.+)$/gm, '<li>$1</li>');

    // Ordered lists
    html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');

    // Wrap consecutive <li> in <ul>
    html = html.replace(/((?:<li[^>]*>(?:[\s\S](?!<\/li>))*.<\/li>\s*)+)/g, '<ul>$1</ul>');

    // Horizontal rules
    html = html.replace(/^---$/gm, '<hr>');

    // Links
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener" style="color:var(--text-link);">$1</a>');

    // Paragraphs (wrap remaining non-tag lines)
    html = html.replace(/^(?!<[hupbl]|<li|<hr|<code|<pre|<ul|<ol|<blockquote|%%)(\S.*)$/gm, '<p>$1</p>');

    // Clean up empty paragraphs
    html = html.replace(/<p>\s*<\/p>/g, '');

    // Convert double newlines to breaks
    html = html.replace(/\n\n/g, '<br>');
    // Clean up remaining single newlines between tags
    html = html.replace(/\n/g, ' ');

    // Restore tables, code blocks and inline codes (function replacer avoids $-interpretation)
    tables.forEach((t, i) => { html = html.replace(`%%TABLE_${i}%%`, () => t); });
    codeBlocks.forEach((block, i) => {
        html = html.replace(`%%CODEBLOCK_${i}%%`, block);
    });
    inlineCodes.forEach((code, i) => {
        html = html.replace(`%%INLINE_${i}%%`, code);
    });

    return html;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ============================================================
// UTILITY FUNCTIONS
// ============================================================
function scrollToBottom() {
    requestAnimationFrame(() => {
        els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
    });
}

function setStatus(text, active) {
    els.chatStatus.innerHTML = active
        ? `<div class="spinner" style="width:8px;height:8px;border-width:1.5px;"></div> ${text}`
        : `<span class="status-dot"></span> ${text}`;
}

// ============================================================
// TOAST NOTIFICATIONS
// ============================================================
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;

    const icon = type === 'success' ? '✅' : type === 'error' ? '❌' : 'ℹ️';
    toast.innerHTML = `<span>${icon}</span><span>${message}</span>`;

    els.toastContainer.appendChild(toast);

    // Auto-remove after 4 seconds
    setTimeout(() => {
        toast.classList.add('toast-exit');
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

// ============================================================
// EDU REVOLUTION — NOMINATION / REGISTRATION FLOW
// ============================================================
async function initApply() {
    if (!els.applyBtn) return;
    els.applyBtn.addEventListener('click', openApplyFromButton);
    if (els.applyBtnSide) els.applyBtnSide.addEventListener('click', () => { openApplyFromButton(); closeSidebar(); });
    els.applyClose.addEventListener('click', closeApplyModal);
    els.applyCancel.addEventListener('click', closeApplyModal);
    els.applyModal.addEventListener('click', (e) => {
        if (e.target === els.applyModal) closeApplyModal();
    });
    els.applyForm.addEventListener('submit', submitApplication);
    els.initiativeSelect.addEventListener('change', updateInitiativeHint);
    const benefitSel = els.applyForm.querySelector('select[name="academic_benefit"]');
    if (benefitSel) benefitSel.addEventListener('change', updateRequiredFields);
    initProofUpload();
    if (els.verifyBtn) els.verifyBtn.addEventListener('click', verifyIdentity);
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && els.applyModal.classList.contains('open')) closeApplyModal();
    });
    await loadInitiatives();
}

async function loadInitiatives() {
    try {
        const r = await fetch(API.initiatives);
        const d = await r.json();
        INITIATIVES = d.initiatives || [];
        BENEFITS = d.academic_benefits || [];
        YEARS = d.years || [];
        REQUIREMENTS = d.requirements || {};
        fillSelect('select[name="initiative"]', INITIATIVES.map(i => [i.key, i.label]), 'Select an initiative…');
        fillSelect('select[name="academic_benefit"]', BENEFITS.map(b => [b.key, b.label]), 'Select a benefit…');
        fillSelect('select[name="year_of_study"]', YEARS.map(y => [y, y]), 'Select…');
        // Show the real upload limits coming from the server
        if (els.proofHint && d.proof_upload) {
            const exts = (d.proof_upload.allowed_extensions || []).join(', ');
            els.proofHint.textContent = `${exts} · up to ${d.proof_upload.max_mb}MB each`;
        }
    } catch (e) {
        console.warn('Could not load initiatives:', e);
    }
}

function fillSelect(selector, pairs, placeholder) {
    const el = els.applyForm.querySelector(selector);
    if (!el) return;
    el.innerHTML = `<option value="">${escapeHtml(placeholder)}</option>` +
        pairs.map(([v, l]) => `<option value="${escapeHtml(v)}">${escapeHtml(l)}</option>`).join('');
}

function updateInitiativeHint() {
    const found = INITIATIVES.find(i => i.key === els.initiativeSelect.value);
    els.initiativeHint.textContent = found ? found.hint : '';
    updateRequiredFields();
}

// Mark the metric field that is compulsory for the CURRENTLY selected filing.
const METRIC_FIELDS = ['revenue_amount', 'stipend_amount', 'duration_months'];
function updateRequiredFields() {
    const init = (els.applyForm.querySelector('[name="initiative"]') || {}).value || '';
    const ben = (els.applyForm.querySelector('[name="academic_benefit"]') || {}).value || '';
    const need = REQUIREMENTS[`${init}|${ben}`] || null;
    METRIC_FIELDS.forEach(m => setMetricRequired(m, m === need));
}
function setMetricRequired(name, required) {
    const span = els.applyForm.querySelector(`[data-label-for="${name}"]`);
    const input = els.applyForm.querySelector(`[name="${name}"]`);
    if (span) {
        const base = span.textContent.replace(/\s*\*\s*$/, '').trim();
        span.innerHTML = escapeHtml(base) + (required ? ' <em>*</em>' : '');
    }
    if (input) {
        input.classList.toggle('metric-required', required);
        if (required && (input.placeholder || '').startsWith('for ')) input.placeholder = 'Required for this filing';
    }
}

// ---- Identity verification against college records ----
async function verifyIdentity() {
    const regId = (els.verifyRegId.value || '').trim();
    const secret = (els.verifySecret.value || '').trim();
    if (!regId || !secret) {
        els.verifyStatus.textContent = 'Enter your registration ID and date of birth.';
        els.verifyStatus.className = 'verify-status err';
        return;
    }
    els.verifyBtn.disabled = true;
    els.verifyStatus.textContent = 'Verifying…';
    els.verifyStatus.className = 'verify-status';
    try {
        const r = await fetch(API.login, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ registration_id: regId, secret }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.detail || 'Verification failed');

        const s = d.student || {};
        setField('student_name', s.name);
        setField('registration_id', s.registration_id);
        setField('email', s.email);
        setField('program', s.program);
        setField('school', s.school);
        setSelectField('year_of_study', s.year_of_study);
        // Authoritative CGPA/attendance — fill AND lock (read-only) so they can't be edited.
        lockField('cgpa', s.cgpa);
        lockField('attendance_percent', s.attendance_percent);
        state.identityVerified = true;

        els.verifyStatus.innerHTML = `✅ Verified as <b>${escapeHtml(s.name)}</b> — CGPA ${s.cgpa} · attendance ${s.attendance_percent}% pulled from college records (locked).`;
        els.verifyStatus.className = 'verify-status ok';
        renderNudges(d.nudges);
    } catch (err) {
        els.verifyStatus.textContent = '❌ ' + err.message;
        els.verifyStatus.className = 'verify-status err';
    } finally {
        els.verifyBtn.disabled = false;
    }
}

function setField(name, val) {
    const f = els.applyForm.querySelector(`[name="${name}"]`);
    if (f && val != null && val !== '') { f.value = val; }
}
function setSelectField(name, val) {
    const f = els.applyForm.querySelector(`select[name="${name}"]`);
    if (f && val && Array.from(f.options).some(o => o.value === val)) f.value = val;
}
function lockField(name, val) {
    const f = els.applyForm.querySelector(`[name="${name}"]`);
    if (!f) return;
    if (val != null) f.value = val;
    f.readOnly = true;
    f.classList.add('locked');
    f.title = 'From official college records — verified';
}

function unlockIdentity() {
    state.identityVerified = false;
    ['cgpa', 'attendance_percent'].forEach(name => {
        const f = els.applyForm.querySelector(`[name="${name}"]`);
        if (f) { f.readOnly = false; f.classList.remove('locked'); f.title = ''; }
    });
    if (els.verifyStatus) { els.verifyStatus.textContent = ''; els.verifyStatus.className = 'verify-status'; }
    if (els.verifyNudges) { els.verifyNudges.hidden = true; els.verifyNudges.innerHTML = ''; }
    if (els.verifyRegId) els.verifyRegId.value = '';
    if (els.verifySecret) els.verifySecret.value = '';
}

function renderNudges(nudges) {
    if (!nudges || !els.verifyNudges) return;
    const opps = (nudges.opportunities || []).slice(0, 5).map(o => o.benefit_label);
    if (!opps.length) { els.verifyNudges.hidden = true; return; }
    els.verifyNudges.hidden = false;
    els.verifyNudges.innerHTML =
        `💡 <b>You may qualify for:</b> ${opps.map(escapeHtml).join(', ')}` +
        (nudges.opportunities.length > 5 ? ` +${nudges.opportunities.length - 5} more` : '') +
        ` — pick one above and I'll help you file it.`;
}

// ---- Proof document upload ----
function initProofUpload() {
    if (!els.proofDrop) return;
    els.proofDrop.addEventListener('click', (e) => {
        // The <input> lives inside the drop zone, so input.click() bubbles back here.
        // Ignore that re-entrant click, otherwise the browser blocks the file picker.
        if (e.target === els.proofInput) return;
        els.proofInput.click();
    });
    els.proofInput.addEventListener('click', (e) => e.stopPropagation());
    els.proofInput.addEventListener('change', (e) => {
        uploadProofs(Array.from(e.target.files || []));
        e.target.value = '';   // allow re-selecting the same file
    });
    ['dragover', 'dragenter'].forEach(ev =>
        els.proofDrop.addEventListener(ev, (e) => { e.preventDefault(); els.proofDrop.classList.add('drag-over'); }));
    ['dragleave', 'drop'].forEach(ev =>
        els.proofDrop.addEventListener(ev, () => els.proofDrop.classList.remove('drag-over')));
    els.proofDrop.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadProofs(Array.from(e.dataTransfer.files || []));
    });
}

async function uploadProofs(files) {
    if (!files.length) return;
    const fd = new FormData();
    files.forEach(f => fd.append('files', f));

    els.proofDrop.classList.add('uploading');
    renderProofChips(files.map(f => ({ filename: f.name, uploading: true })));
    try {
        const r = await fetch(API.proofs, { method: 'POST', body: fd });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) {
            const errs = (d.detail && d.detail.errors) || [];
            throw new Error(errs[0] || (d.detail && d.detail.message) || `Upload failed (${r.status})`);
        }
        (d.proofs || []).forEach(p => state.proofs.push(p));
        (d.errors || []).forEach(msg => showToast(msg, 'error'));
        if ((d.proofs || []).length) {
            showToast(`📎 ${d.proofs.length} document(s) attached`, 'success');
        }
    } catch (err) {
        showToast(err.message, 'error');
    } finally {
        els.proofDrop.classList.remove('uploading');
        renderProofChips();
    }
}

function renderProofChips(pending = []) {
    if (!els.proofChips) return;
    const kb = (n) => n >= 1048576 ? `${(n / 1048576).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1024))}KB`;
    const chips = state.proofs.map((p, i) => `
        <span class="proof-chip" title="${escapeHtml(p.filename)}">
            <span class="proof-chip-name">📄 ${escapeHtml(p.filename)}</span>
            <span class="proof-chip-meta">${kb(p.size_bytes || 0)}${p.extracted_chars ? ' · text read' : ''}</span>
            <button type="button" class="proof-chip-x" data-i="${i}" title="Remove">✕</button>
        </span>`).join('');
    const pend = pending.map(p => `
        <span class="proof-chip pending"><span class="proof-chip-name">⏳ ${escapeHtml(p.filename)}</span></span>`).join('');
    els.proofChips.innerHTML = chips + pend;
    els.proofChips.querySelectorAll('.proof-chip-x').forEach(b => b.addEventListener('click', () => {
        state.proofs.splice(+b.dataset.i, 1);
        renderProofChips();
    }));
}

// --- The agentic hand-off: read the chat metadata and act ---
function handleApplicationSignals(metadata, msgEl) {
    if (!metadata) return;
    if (metadata.prefill && Object.keys(metadata.prefill).length) mergePrefill(metadata.prefill);

    if (metadata.action === 'start_application') {
        openApplyModal(state.prefill);
    } else if (metadata.offer_application) {
        renderOfferCta(msgEl);
    }
}

function renderOfferCta(msgEl) {
    if (!msgEl) return;
    const contentEl = msgEl.querySelector('.message-content');
    const wrap = contentEl ? contentEl.parentElement : null;
    if (!wrap || wrap.querySelector('.cta-row')) return;

    const row = document.createElement('div');
    row.className = 'cta-row';
    const btn = document.createElement('button');
    btn.className = 'cta-btn';
    btn.innerHTML = '📝 File this nomination now';
    btn.addEventListener('click', () => openApplyModal(state.prefill));
    row.appendChild(btn);
    wrap.appendChild(row);
    scrollToBottom();
}

function mergePrefill(obj) {
    if (!obj) return;
    Object.entries(obj).forEach(([k, v]) => {
        if (v !== null && v !== undefined && v !== '') state.prefill[k] = v;
    });
}

async function openApplyFromButton() {
    openApplyModal(state.prefill);
    // Best-effort: mine the server-side conversation for anything else to prefill.
    try {
        const r = await fetch(API.prefill, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: state.sessionId }),
        });
        const d = await r.json();
        if (d.prefill && Object.keys(d.prefill).length) {
            mergePrefill(d.prefill);
            applyPrefill(state.prefill);
        }
    } catch (e) { /* ignore — form still works empty */ }
}

function openApplyModal(prefill) {
    clearFieldErrors();
    els.applyFormMsg.textContent = '';
    if (prefill) applyPrefill(prefill);
    els.applyModal.classList.add('open');
    els.applyModal.setAttribute('aria-hidden', 'false');
    const fields = Array.from(els.applyForm.querySelectorAll('input, select, textarea'));
    const firstEmpty = fields.find(f => f.type !== 'checkbox' && !f.value);
    setTimeout(() => (firstEmpty || fields[0]).focus(), 60);
}

function closeApplyModal() {
    els.applyModal.classList.remove('open');
    els.applyModal.setAttribute('aria-hidden', 'true');
}

function applyPrefill(obj) {
    if (!obj) return;
    let filled = false;
    Object.entries(obj).forEach(([k, v]) => {
        if (v === null || v === undefined || v === '') return;
        const field = els.applyForm.querySelector(`[name="${k}"]`);
        if (!field) return;
        if (field.tagName === 'SELECT') {
            const val = String(v);
            if (Array.from(field.options).some(o => o.value === val)) {
                field.value = val;
                filled = true;
            }
        } else if (field.type !== 'checkbox') {
            field.value = v;
            filled = true;
        }
    });
    updateInitiativeHint();
    if (filled) els.prefillNote.hidden = false;
}

function clearFieldErrors() {
    $$('.field-error').forEach(e => { e.textContent = ''; e.classList.remove('show'); });
    els.applyForm.querySelectorAll('.invalid').forEach(f => f.classList.remove('invalid'));
    els.applyFormMsg.className = 'modal-footer-msg';
}

function showFieldErrors(errors) {
    Object.entries(errors).forEach(([field, msg]) => {
        const err = els.applyForm.querySelector(`.field-error[data-error-for="${field}"]`);
        if (err) { err.textContent = msg; err.classList.add('show'); }
        const input = els.applyForm.querySelector(`[name="${field}"]`);
        if (input) input.classList.add('invalid');
    });
    const first = els.applyForm.querySelector('.invalid');
    if (first) first.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function setApplyLoading(on) {
    els.applySubmit.disabled = on;
    els.applySubmit.querySelector('.btn-label').hidden = on;
    els.applySubmit.querySelector('.btn-spinner').hidden = !on;
}

async function submitApplication(e) {
    e.preventDefault();
    clearFieldErrors();
    els.applyFormMsg.textContent = '';

    const fd = new FormData(els.applyForm);
    const payload = {};
    // Skip empty values so blank optional fields aren't sent as "" (which the server
    // can't coerce to a number). Empty REQUIRED fields are still caught server-side.
    fd.forEach((v, k) => { if (typeof v === 'string' ? v.trim() !== '' : v != null) payload[k] = v; });
    payload.declaration = els.applyForm.querySelector('[name="declaration"]').checked;
    // Attach the already-uploaded proof documents by id (the server resolves them).
    payload.proof_files = state.proofs.map(p => p.proof_id);
    payload.identity_verified = state.identityVerified;

    setApplyLoading(true);
    try {
        const r = await fetch(API.register, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await r.json().catch(() => ({}));

        if (r.status === 422) {
            const detail = data.detail;
            let errs = {};
            if (detail && detail.errors) {
                errs = detail.errors;                       // our per-field shape
            } else if (Array.isArray(detail)) {
                // FastAPI's default validation shape: [{loc:[...,field], msg}]
                detail.forEach(e => {
                    const f = (e.loc || []).slice(-1)[0];
                    if (f) errs[f] = e.msg;
                });
            }
            showFieldErrors(errs);
            const msgs = Object.values(errs);
            // Spell out exactly what's wrong instead of a vague "fix the highlighted fields".
            els.applyFormMsg.textContent = msgs.length
                ? `Please fix: ${msgs.join('  ')}`
                : ((detail && detail.message) || 'Please fix the highlighted fields.');
            els.applyFormMsg.className = 'modal-footer-msg error';
            showToast(msgs[0] || 'Please complete the required fields', 'error');
            return;
        }
        if (!r.ok) {
            const msg = (data.detail && (data.detail.message || data.detail)) || `Server error (${r.status})`;
            throw new Error(typeof msg === 'string' ? msg : 'Registration failed');
        }

        // Success — confirm in chat with the reference number + next steps.
        closeApplyModal();
        const welcomeEl = document.getElementById('welcomeScreen');
        if (welcomeEl) welcomeEl.style.display = 'none';
        addMessage('assistant', buildSuccessCard(data));
        showToast(`✅ Nomination filed — ${data.reference_id}`, 'success');
        els.applyForm.reset();
        els.prefillNote.hidden = true;
        state.prefill = {};
        state.proofs = [];
        renderProofChips();
        unlockIdentity();

    } catch (err) {
        els.applyFormMsg.textContent = err.message;
        els.applyFormMsg.className = 'modal-footer-msg error';
        showToast(err.message, 'error');
    } finally {
        setApplyLoading(false);
    }
}

function buildSuccessCard(rec) {
    const steps = rec.next_steps || [];
    const dec = rec.decision || {};
    const outcomeMeta = {
        auto_approve: ['🟢', 'Auto-approved'],
        auto_reject: ['🔴', 'Auto-rejected'],
        escalate: ['🟡', 'Sent for review'],
    }[dec.outcome] || ['📥', 'Submitted'];

    let md = `# ✅ Nomination Filed — ${rec.reference_id}\n\n`;
    md += `**Applicant:** ${rec.student_name} (${rec.registration_id})\n\n`;
    md += `**Pathway:** ${rec.initiative_label || rec.initiative} → ${rec.academic_benefit_label || rec.academic_benefit}\n\n`;

    // Automated decision
    md += `### ${outcomeMeta[0]} EDURev decision: ${outcomeMeta[1]}\n`;
    if (dec.reason) md += `- ${dec.reason}\n`;
    if (rec.current_owner_label && dec.outcome === 'escalate') {
        md += `- Now with: **${rec.current_owner_label}**`;
        if (rec.sla_due) md += ` (target within the SLA)`;
        md += `\n`;
    }
    if (typeof dec.confidence === 'number') md += `- Confidence: ${Math.round(dec.confidence * 100)}%\n`;

    // Attached proof documents + what the verification check found in them
    const proofs = rec.proof_files || [];
    if (proofs.length) {
        md += `\n### 📎 Documents received & scanned\n`;
        md += `- ${proofs.map(p => `**${p.filename}**`).join(', ')}\n`;
        const v = rec.verification || {};
        const ids = v.identifiers ? Object.entries(v.identifiers) : [];
        if (ids.length) {
            const list = ids.map(([k, vals]) => `${k.toUpperCase()} ${vals.join(', ')}`).join('; ');
            md += `- 🔎 Verifiable IDs found: **${list}** (pending confirmation with the issuing source)\n`;
        } else {
            md += `- 🔎 Scanned — no machine-verifiable ID (DOI / patent / certificate) inside; a reviewer will verify it manually.\n`;
        }
    }

    // Duplicate/fraud note if any
    const dup = rec.duplicate_check || {};
    if (dup.risk && dup.risk !== 'none') {
        md += `\n> ⚠️ Duplicate/fraud check: **${dup.risk}** — ${(dup.reasons || [])[0] || ''}\n`;
    }

    md += `\n### 🗺️ Your next steps\n`;
    steps.forEach((s, i) => { md += `${i + 1}. ${s}\n`; });
    md += `\n> Keep your reference **${rec.reference_id}** safe — track its status via the EDU Revolution portal.`;
    return md;
}

// ============================================================
// HEALTH CHECK ON LOAD
// ============================================================
(async function healthCheck() {
    try {
        const response = await fetch(API.health);
        const data = await response.json();
        console.log('🎓 EdU Revolution Health:', data);
    } catch (error) {
        console.warn('⚠️ Backend not reachable:', error.message);
    }
})();
