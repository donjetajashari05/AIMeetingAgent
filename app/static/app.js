'use strict';

/* ================= Helpers ================= */

const ICONS = {
  wave: '<path d="M4 10v4M8 6v12M12 9v6M16 4v16M20 10v4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  grid: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  check: '<path d="M9 11l3 3 8-8"/><path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9"/>',
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  back: '<path d="M15 6l-6 6 6 6"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5M4 20h16"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  tick: '<path d="M5 12l5 5 9-10"/>',
  alert: '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9L2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3"/>',
  retry: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8M21 3v5h-5"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14.5a6.5 6.5 0 0 1 3.5 5.5"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="M13.5 6.5l4 4"/>',
  file: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
  jira: '<path d="M12 3l9 9-9 9-9-9z"/><path d="M12 8l4 4-4 4-4-4z"/>',
};

function icon(name, size = 18, strokeWidth = 1.9) {
  const span = document.createElement('span');
  span.setAttribute('aria-hidden', 'true');
  span.style.display = 'inline-flex';
  // Only constant strings from ICONS are inserted as markup
  span.innerHTML = `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${strokeWidth}" stroke-linecap="round" stroke-linejoin="round">${ICONS[name] || ''}</svg>`;
  return span;
}

/** el('div', {class: 'x', onclick: fn, text: '...'}, child1, child2) — all text via textContent. */
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (k in node && typeof v !== 'string') node[k] = v;
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

/** Drop null/false before append()/replaceChildren(), which would print them as text. */
const nn = (...xs) => xs.flat(Infinity).filter((x) => x !== null && x !== undefined && x !== false);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: opts.body && !(opts.body instanceof FormData) ? { 'Content-Type': 'application/json' } : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    showLogin();  // session expired or not logged in
    throw new Error('Hyr përsëri.');
  }
  if (!res.ok) throw new Error((data && data.detail) || `Gabim ${res.status}`);
  return data;
}

function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove('show'), 3200);
}

/* ================= Formatting (Albanian) ================= */

const MONTHS = ['jan', 'shk', 'mar', 'pri', 'maj', 'qer', 'kor', 'gus', 'sht', 'tet', 'nën', 'dhj'];
const MONTHS_FULL = ['janar', 'shkurt', 'mars', 'prill', 'maj', 'qershor', 'korrik', 'gusht', 'shtator', 'tetor', 'nëntor', 'dhjetor'];
const DAYS = ['e diel', 'e hënë', 'e martë', 'e mërkurë', 'e enjte', 'e premte', 'e shtunë'];
// Readable on white (text ≥ 4.5:1) and as avatar backgrounds under white text
const SPEAKER_COLORS = ['#683790', '#17794E', '#946200', '#B8336A', '#1F7A99', '#C2410C'];

const parseDate = (iso) => { const [y, m, d] = iso.split('-').map(Number); return new Date(y, m - 1, d); };
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const fmtLong = (iso) => { const d = parseDate(iso); return `${cap(DAYS[d.getDay()])}, ${d.getDate()} ${MONTHS_FULL[d.getMonth()]} ${d.getFullYear()}`; };
const fmtShort = (iso) => { const d = parseDate(iso); return `${d.getDate()} ${MONTHS[d.getMonth()]}`; };
const daysBetween = (a, b) => Math.round((parseDate(b) - parseDate(a)) / 86400000);

function fmtClock(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const mm = String(m).padStart(2, '0'), ss = String(s).padStart(2, '0');
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function fmtDuration(sec) {
  if (!sec) return null;
  const m = Math.round(sec / 60);
  if (m < 1) return `${Math.round(sec)} sek`;
  return m >= 60 ? `${Math.floor(m / 60)} orë ${String(m % 60).padStart(2, '0')} min` : `${m} min`;
}

function duePill(task) {
  if (task.status === 'done') return el('span', { class: 'pill done', text: 'Kryer' });
  if (!task.due_date) return el('span', { class: 'pill', text: 'Pa afat' });
  const diff = daysBetween(state.today, task.due_date);
  if (diff < 0) return el('span', { class: 'pill late', text: `Vonuar ${-diff} ditë` });
  if (diff === 0) return el('span', { class: 'pill today', text: 'Sot' });
  if (diff === 1) return el('span', { class: 'pill today', text: 'Nesër' });
  const d = parseDate(task.due_date);
  const label = diff < 7 ? `${cap(DAYS[d.getDay()])}, ${fmtShort(task.due_date)}` : fmtShort(task.due_date);
  return el('span', { class: 'pill soon', text: label, title: task.due_date });
}

const STATUS = {
  queued: ['Në radhë', 'work'],
  transcribing: ['Transkriptim', 'work'],
  diarizing: ['Folësit', 'work'],
  extracting: ['Analiza', 'work'],
  done: ['Përpunuar', 'done'],
  needs_key: ['Pa analizë', 'warn'],
  error: ['Gabim', 'error'],
};
const isBusy = (m) => ['queued', 'transcribing', 'diarizing', 'extracting'].includes(m.status);

function statusChip(m) {
  const [label, cls] = STATUS[m.status] || [m.status, 'work'];
  const text = m.status === 'transcribing' ? `${label} ${Math.round((m.progress || 0) * 100)}%` : label;
  return el('span', { class: `status ${cls}`, text });
}

/* ================= State & router ================= */

const state = { me: null, status: null, org: { employees: [], teams: [], projects: [] }, today: new Date().toISOString().slice(0, 10), poll: null, cleanup: null };

const isAdmin = () => !!(state.me && state.me.is_admin);
const isManager = () => !!(state.me && (state.me.is_admin || state.me.is_pm));
const ROLE_LABEL = { admin: 'Administrator', pm: 'Project manager', employee: 'Punonjës' };

async function loadOrg() {
  state.org = await api('/api/org');
  return state.org;
}

const byId = (list, id) => list.find((x) => x.id === id);
const initials = (name) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('');
const personColor = (id) => SPEAKER_COLORS[id % SPEAKER_COLORS.length];
const view = document.getElementById('view');

async function loadStatus() {
  state.status = await api('/api/status');
  state.today = state.status.today;
  const set = (id, ok, okText, offText) => {
    document.getElementById(id).className = ok ? 'dot' : 'dot warn';
    document.getElementById(`${id}-t`).textContent = ok ? okText : offText;
  };
  set('st-claude', state.status.claude, 'lidhur', 'pa key');
  set('st-speakers', state.status.speakers, 'aktiv', 'pa token');
  set('st-email', state.status.email, 'lidhur', 'test');
  set('st-jira', state.status.jira, state.status.jira_mock ? 'test' : 'lidhur', 'pa token');
  const jiraRow = document.getElementById('st-jira').parentElement;
  if (state.status.jira_url && jiraRow.tagName !== 'A') {
    const link = el('a', { class: jiraRow.className, href: state.status.jira_url, target: '_blank', rel: 'noopener', title: 'Hap Jira' });
    link.append(...jiraRow.childNodes);
    jiraRow.replaceWith(link);
  }
}

async function refreshOpenCount() {
  try {
    const tasks = await api('/api/tasks?status=open');
    document.getElementById('open-count').textContent = tasks.length || '';
  } catch { /* ignore */ }
}

function stopPolling() {
  clearInterval(state.poll);
  state.poll = null;
}

function poll(fn, ms = 2500) {
  stopPolling();
  state.poll = setInterval(fn, ms);
}

async function route() {
  stopPolling();
  if (state.cleanup) { state.cleanup(); state.cleanup = null; }
  const hash = location.hash.replace(/^#\/?/, '').split('?')[0];
  const [name, id] = hash.split('/');
  const key = name === 'meeting' ? 'meetings' : (name || 'home');
  document.querySelectorAll('.nav a').forEach((a) => a.classList.toggle('active', a.dataset.route === key));

  view.replaceChildren(el('div', { class: 'page' }, el('div', { class: 'thinking' }, el('span', { class: 'spinner' }), 'Duke u ngarkuar…')));
  try {
    if (name === 'meeting' && id) await renderMeeting(Number(id));
    else if (name === 'meetings') await renderMeetings();
    else if (name === 'tasks') await renderTasks();
    else if (name === 'ask') renderAsk();
    else if (name === 'team' && isAdmin()) await renderTeam();
    else if (name === 'outbox' && isAdmin()) await renderOutbox();
    else await renderHome();
  } catch (e) {
    view.replaceChildren(el('div', { class: 'page' }, notice('error', 'alert', e.message)));
  }
  refreshOpenCount();
}

window.addEventListener('hashchange', () => { route(); view.focus({ preventScroll: true }); window.scrollTo(0, 0); });

/* ================= Shared pieces ================= */

function notice(kind, iconName, ...content) {
  return el('div', { class: `notice ${kind}` }, icon(iconName, 18), el('div', { class: 'grow' }, ...content));
}

function keyNotice() {
  if (state.status.claude) return null;
  return notice('warn', 'key',
    el('strong', { text: 'Claude API s’është lidhur. ' }),
    'Transkriptimi punon, por për përmbledhjen, vendimet, detyrat dhe pyetjet shto ',
    el('code', { class: 'mono', text: 'ANTHROPIC_API_KEY=sk-ant-…' }),
    ' në skedarin .env dhe rinise serverin.');
}

function speakerColors(lines) {
  const map = new Map();
  for (const l of lines) if (l.speaker && !map.has(l.speaker)) map.set(l.speaker, SPEAKER_COLORS[map.size % SPEAKER_COLORS.length]);
  return map;
}

function assigneeSelect(task, onDone) {
  const people = state.org.employees.filter((e) => e.active || e.id === task.assignee_id);
  const sel = el('select', {
    class: 'assign', 'aria-label': `Përgjegjësi për: ${task.description}`,
    onchange: async () => {
      sel.disabled = true;
      try {
        const id = sel.value ? Number(sel.value) : null;
        const updated = await api(`/api/tasks/${task.id}`, { method: 'PATCH', body: JSON.stringify({ assignee_id: id, set_assignee: true }) });
        Object.assign(task, updated);
        toast(id ? `Detyra iu caktua: ${updated.assignee_name}` : 'Lidhja me punonjësin u hoq.');
        onDone();
      } catch (e) { toast(e.message); sel.disabled = false; }
    },
  },
  el('option', { value: '', text: task.assignee_id ? '— Pa lidhje —' : `${task.assignee || 'Pa përgjegjës'} (pa lidhje)` }),
  people.map((e) => el('option', { value: e.id, text: e.full_name, selected: e.id === task.assignee_id })));
  return sel;
}

/** Add or edit a task (PM only). Saving a task also confirms it: the PM has looked at it. */
function taskForm(meetingId, task, reload) {
  const t = task || { description: '', assignee_id: null, due_date: null };
  const desc = el('textarea', { rows: '3', maxlength: '1000', required: true, placeholder: 'Çka duhet bërë?' });
  desc.value = t.description;
  const who = el('select', {},
    el('option', { value: '', text: t.assignee && !t.assignee_id ? `${t.assignee} (pa lidhje)` : '— Pa përgjegjës —' }),
    state.org.employees.filter((e) => e.active || e.id === t.assignee_id)
      .map((e) => el('option', { value: e.id, text: e.full_name, selected: e.id === t.assignee_id })));
  const due = el('input', { type: 'date', value: t.due_date || '' });
  openForm(task ? 'Ndrysho detyrën' : 'Detyrë e re', [
    el('div', { class: 'field-grid' },
      field('Detyra', desc),
      field('Përgjegjësi', who, { wide: false }),
      field('Afati', due, { wide: false, hint: t.due_date_text ? `Në mbledhje u tha: „${t.due_date_text}”` : null })),
  ], {
    saveText: task ? 'Ruaj dhe konfirmo' : 'Shto detyrën',
    onSave: async () => {
      const assignee = who.value ? Number(who.value) : null;
      if (task) {
        await api(`/api/tasks/${task.id}`, { method: 'PATCH', body: JSON.stringify({
          description: desc.value, due_date: due.value || null, set_due_date: true, approved: true,
          ...(assignee !== task.assignee_id ? { assignee_id: assignee, set_assignee: true } : {}) }) });
        toast('Detyra u ruajt dhe u konfirmua.');
      } else {
        await api(`/api/meetings/${meetingId}/tasks`, { method: 'POST', body: JSON.stringify({
          description: desc.value, due_date: due.value || null, assignee_id: assignee }) });
        toast('Detyra u shtua.');
      }
      reload();
    },
    onDelete: task && !task.jira_key ? async () => {
      if (!confirm(`Ta fshij detyrën “${task.description}”?`)) return false;
      await api(`/api/tasks/${task.id}`, { method: 'DELETE' });
      toast('Detyra u fshi.');
      reload();
    } : null,
  });
}

function taskRow(task, { showMeeting = false, onChange, assignable = false, readOnly = false, manage = null } = {}) {
  const row = el('div', { class: `task${task.status === 'done' ? ' is-done' : ''}${!task.approved_at && !task.jira_key ? ' is-draft' : ''}` });
  const box = el('input', {
    type: 'checkbox', checked: task.status === 'done', 'aria-label': `Shëno si të kryer: ${task.description}`,
    disabled: readOnly, title: readOnly ? 'Vetëm personi përgjegjës ose PM-ja e ndryshon' : null,
    onchange: async () => {
      box.disabled = true;
      try {
        const updated = await api(`/api/tasks/${task.id}`, { method: 'PATCH', body: JSON.stringify({ status: box.checked ? 'done' : 'open' }) });
        Object.assign(task, updated);
        row.replaceWith(taskRow(task, { showMeeting, onChange, assignable, readOnly, manage }));
        refreshOpenCount();
        if (onChange) onChange(task);
      } catch (e) { box.checked = !box.checked; box.disabled = false; toast(e.message); }
    },
  });
  const name = task.assignee_name || task.assignee;
  const who = assignable && state.org.employees.length
    ? assigneeSelect(task, () => row.replaceWith(taskRow(task, { showMeeting, onChange, assignable, readOnly, manage })))
    : name
      ? el('span', { class: 'who', title: task.assignee_id ? 'I lidhur me listën e punonjësve' : 'Emri nga transkripti' },
        task.assignee_id ? el('span', { class: 'dot', style: { background: personColor(task.assignee_id) } }) : icon('user', 14), name)
      : el('span', { class: 'subtle', text: 'Pa përgjegjës' });
  const draft = !task.approved_at && !task.jira_key;
  const meta = el('div', { class: 'meta' },
    draft ? el('span', { class: 'draft-pill', text: 'Draft', title: 'E nxjerrë nga AI-ja: PM-ja duhet ta konfirmojë' })
      : !task.jira_key ? el('span', { class: 'ok-pill', text: 'Konfirmuar' }) : null,
    who,
    duePill(task),
    task.due_date_text ? el('span', { class: 'subtle', text: `„${task.due_date_text}”` }) : null,
    task.jira_key ? el('a', { class: 'jira-link', href: task.jira_url, target: '_blank', rel: 'noopener', text: task.jira_key, title: 'Hape në Jira' }) : null,
    !task.jira_key && task.jira_error ? el('span', { class: 'jira-error', text: `Jira: ${task.jira_error}` }) : null,
    showMeeting ? el('a', { href: `#/meeting/${task.meeting_id}`, class: 'small', text: task.meeting_title }) : null,
  );
  row.append(box, el('div', { class: 'grow' }, el('span', { class: 'desc', text: task.description }), meta));
  if (manage && !task.jira_key) {
    row.append(el('div', { class: 'task-actions' },
      draft ? el('button', { type: 'button', class: 'btn btn-sm btn-primary', title: 'Konfirmo detyrën ashtu siç është', onclick: async (e) => {
        e.currentTarget.disabled = true;
        try { await api(`/api/tasks/${task.id}`, { method: 'PATCH', body: JSON.stringify({ approved: true }) }); toast('Detyra u konfirmua.'); manage.reload(); }
        catch (err) { toast(err.message); e.currentTarget.disabled = false; }
      } }, icon('tick', 14, 2.4), 'Konfirmo') : null,
      el('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Ndrysho detyrën', title: 'Ndrysho', onclick: () => taskForm(manage.meetingId, task, manage.reload) }, icon('edit', 16))));
  }
  return row;
}

function meetingRow(m) {
  const d = parseDate(m.meeting_date);
  const bits = [fmtDuration(m.duration_sec)];
  if (m.status === 'done' || m.task_count || m.decision_count) {
    bits.push(`${m.task_count} detyra`, `${m.decision_count} vendime`);
  } else if (m.status === 'error') bits.push('përpunimi dështoi');
  else if (isBusy(m)) bits.push(m.note || 'po përpunohet');
  else if (m.status === 'needs_key') bits.push('vetëm transkripti');
  if (m.language) bits.push(m.language.toUpperCase());
  if (m.project_name || m.team_name) bits.unshift(m.project_name || m.team_name);
  return el('a', { class: 'meeting-row', href: `#/meeting/${m.id}` },
    el('div', { class: 'date-tile' }, el('b', { text: d.getDate() }), el('span', { text: MONTHS[d.getMonth()] })),
    el('div', { class: 'grow' },
      el('span', { class: 'name', text: m.title }),
      el('span', { class: 'small subtle', text: bits.filter(Boolean).join(' · ') })),
    statusChip(m),
    el('span', { class: 'row-arrow' }, icon('arrow', 16, 2)));
}

/* ---------- Ask (RAG) ---------- */

const EXAMPLES = ['Çka vendosëm për buxhetin?', 'Cilat detyra i ka Arta?', 'Çka mbeti pa zgjidhje?'];

function renderAnswer(container, res) {
  const parts = res.answer.split(/(\[\d+\])/g).map((p) => (/^\[\d+\]$/.test(p) ? el('span', { class: 'cite', text: p }) : p));
  const used = new Set((res.answer.match(/\[(\d+)\]/g) || []).map((x) => Number(x.slice(1, -1))));
  const sources = res.ai ? res.sources.filter((s) => used.has(s.n)) : res.sources;
  container.replaceChildren(el('div', { class: 'answer' },
    el('div', { class: 'answer-mark' }, icon('wave', 16, 2.2)),
    el('div', { class: 'answer-body' },
      el('p', { class: 'answer-text' }, ...parts),
      sources.length ? el('div', { class: 'source-list' }, sources.map((s) => el('a', {
        class: 'source', href: `#/meeting/${s.meeting_id}${s.start != null ? `?t=${Math.floor(s.start)}` : ''}`,
      },
      el('span', { class: 'n', text: `[${s.n}]` }),
      el('span', {},
        el('span', { class: 'meta', text: `${s.meeting_title} · ${fmtShort(s.meeting_date)}${s.start != null ? ` · ${fmtClock(s.start)}` : ''}` }),
          (s.speaker ? `${s.speaker}: ` : '') + s.text)))) : null)));
}

function askBox({ onAsk, autofocus = false }) {
  const input = el('input', { id: 'ask', type: 'text', placeholder: 'Pyet për çdo mbledhje… p.sh. Çka vendosëm për buxhetin muajin e kaluar?', autocomplete: 'off' });
  const send = el('button', { type: 'submit', 'aria-label': 'Dërgo pyetjen' }, icon('arrow', 18, 2.2));
  const form = el('form', {
    class: 'ask-box',
    onsubmit: (e) => { e.preventDefault(); const q = input.value.trim(); if (q) onAsk(q, input, send); },
  }, icon('search', 20, 2), input, send);
  if (autofocus) setTimeout(() => input.focus(), 0);
  const chips = el('div', { class: 'chips' }, EXAMPLES.map((q) => el('button', {
    type: 'button', class: 'chip', text: q, onclick: () => { input.value = q; form.requestSubmit(); },
  })));
  return { form, input, chips };
}

async function runAsk(q, input, send, out) {
  input.disabled = true; send.disabled = true;
  out.replaceChildren(el('div', { class: 'thinking' }, el('span', { class: 'spinner' }), 'Po kërkoj në mbledhjet e kaluara…'));
  try {
    renderAnswer(out, await api('/api/ask', { method: 'POST', body: JSON.stringify({ question: q }) }));
  } catch (e) {
    out.replaceChildren(notice('error', 'alert', e.message));
  } finally {
    input.disabled = false; send.disabled = false;
  }
}

/* ================= Views ================= */

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? 'Mirëmëngjes' : h < 18 ? 'Mirëdita' : 'Mirëmbrëma';
}

/** Card title with a small icon badge. */
function cardTitle(iconName, text, tone = '') {
  return el('div', { class: 'card-title' }, el('span', { class: `badge ${tone}` }, icon(iconName, 18, 2)), el('h2', { text }));
}

function pageHead(eyebrow, title, ...actions) {
  return el('header', { class: 'page-head' },
    el('div', { class: 'title' }, el('span', { class: 'eyebrow', text: eyebrow }), el('h1', { text: title })),
    actions.length ? el('div', { class: 'actions' }, ...actions) : null);
}

function newMeetingButtons() {
  if (!isManager()) return [];
  return [
    el('button', { type: 'button', class: 'btn', onclick: () => openUpload('file') }, icon('upload', 16, 2), 'Ngarko audio'),
    el('button', { type: 'button', class: 'btn btn-primary', onclick: () => openUpload('record') }, el('span', { class: 'rec-dot' }), 'Regjistro'),
  ];
}

/* ---------- Home ---------- */

async function renderHome() {
  const [meetings, report, tasks] = await Promise.all([api('/api/meetings'), api('/api/report'), api('/api/tasks?status=open')]);

  const out = el('div', { class: 'hero-answer', hidden: true });
  const ask = askBox({ onAsk: (q, i, s) => { out.hidden = false; runAsk(q, i, s, out); } });
  const done = meetings.filter((m) => m.status === 'done').length;
  const hero = el('section', { class: 'hero', 'aria-label': 'Pyet arkivin' },
    el('div', { class: 'hero-top' },
      el('div', { class: 'title' },
        el('span', { class: 'eyebrow', text: fmtLong(state.today) }),
        el('h1', { text: `${greeting()}, ${state.me.full_name.split(' ')[0]}` }),
        el('p', { class: 'hero-sub', text: meetings.length
          ? `${meetings.length} mbledhje në arkiv${done ? `, ${done} të analizuara` : ''}${tasks.length ? ` · ${tasks.length} detyra të hapura` : ''}. Pyet çdo gjë për to.`
          : 'Shto mbledhjen e parë dhe pastaj pyet çdo gjë për të.' })),
      isManager() ? el('div', { class: 'actions' },
        el('button', { type: 'button', class: 'btn btn-glass', onclick: () => openUpload('file') }, icon('upload', 16, 2), 'Ngarko audio'),
        el('button', { type: 'button', class: 'btn btn-yellow', onclick: () => openUpload('record') }, el('span', { class: 'rec-dot' }), 'Regjistro')) : null),
    el('label', { for: 'ask', class: 'sr-only', text: 'Pyet për çdo mbledhje të kaluar' }),
    ask.form, ask.chips, out);

  const processingSlot = el('div', { class: 'col-2 stack' });
  const meetingsSlot = el('div', { class: 'col-3 stack' });

  const paint = (list) => {
    // Employees don't add meetings, so they get their tasks there instead
    processingSlot.replaceChildren(isManager() ? processingCard(list) : openTasksCard(tasks));
    meetingsSlot.replaceChildren(recentMeetingsCard(list));
  };
  paint(meetings);

  view.replaceChildren(el('div', { class: 'page' },
    hero,
    isAdmin() ? keyNotice() : null,
    el('div', { class: 'row' }, el('div', { class: 'col-3 stack' }, reportCard(report)), processingSlot),
    el('div', { class: 'row' }, meetingsSlot, isManager() ? el('div', { class: 'col-2 stack' }, openTasksCard(tasks)) : null),
  ));

  if (meetings.some(isBusy)) {
    poll(async () => {
      const list = await api('/api/meetings');
      paint(list);
      if (!list.some(isBusy)) { stopPolling(); route(); }
    });
  }
}

function reportCard(r) {
  const card = el('section', { class: 'card', 'aria-label': 'Raporti i detyrave' });
  if (!r) {
    card.append(cardTitle('check', 'Raporti para mbledhjes'),
      el('div', { class: 'empty' }, el('strong', { text: 'Ende s’ka detyra' }),
        'Pas mbledhjes së parë, këtu shfaqet sa detyra janë kryer dhe sa janë me vonesë.'));
    return card;
  }
  const total = r.done + r.late + r.open;
  card.append(
    el('div', { class: 'card-head' },
      cardTitle('check', 'Nga mbledhja e kaluar'),
      el('a', { class: 'small link-pill', href: `#/meeting/${r.meeting.id}`, text: `${r.meeting.title} · ${fmtShort(r.meeting.meeting_date)}` })),
    el('div', { class: 'stats' },
      el('div', { class: 'stat done' }, el('b', { text: r.done }), el('span', { text: 'të kryera' })),
      el('div', { class: 'stat late' }, el('b', { text: r.late }), el('span', { text: 'me vonesë' })),
      el('div', { class: 'stat open' }, el('b', { text: r.open }), el('span', { text: 'në punë' }))),
    el('div', { class: 'bar', role: 'img', 'aria-label': `${r.done} të kryera, ${r.late} me vonesë, ${r.open} në punë` },
      r.done ? el('div', { style: { flex: r.done, background: 'var(--green)' } }) : null,
      r.late ? el('div', { style: { flex: r.late, background: 'var(--orange)' } }) : null,
      r.open ? el('div', { style: { flex: r.open, background: 'var(--accent)' } }) : null,
      total === 0 ? el('div', { style: { flex: 1, background: 'var(--surface-3)' } }) : null),
  );
  if (r.late_tasks.length) {
    card.append(el('div', { class: 'list' }, r.late_tasks.map((t) => el('div', { class: 'list-row' },
      duePill(t), el('span', { class: 'grow', text: t.description }), el('span', { class: 'small muted', text: t.assignee_name || '' })))));
  } else if (total > 0) {
    card.append(el('p', { class: 'small muted', text: 'Asnjë detyrë me vonesë.' }));
  }
  return card;
}

function pipelineSteps(m) {
  const order = ['queued', 'transcribing', 'diarizing', 'extracting', 'done'];
  const at = order.indexOf(m.status);
  const st = (i) => (at > i ? 'done' : at === i ? 'active' : 'todo');
  const speakers = state.status.speakers;
  const claude = state.status.claude;
  const steps = [
    { label: 'Transkriptimi', s: m.status === 'queued' ? 'todo' : st(1), meta: m.status === 'transcribing' ? `${Math.round((m.progress || 0) * 100)}%` : '' },
    { label: 'Kush po flet', s: speakers ? st(2) : 'skip', meta: speakers ? '' : 'pa token' },
    { label: 'Vendimet dhe detyrat', s: claude ? st(3) : 'skip', meta: claude ? '' : 'pa key' },
  ];
  return el('ol', { class: 'steps' }, steps.map((x) => el('li', { class: `step ${x.s}` },
    el('span', { class: 'step-dot' }, x.s === 'done' ? icon('tick', 12, 3.5) : null),
    el('span', { text: x.label }), el('span', { class: 'meta', text: x.meta }))));
}

function processingCard(list) {
  const busy = list.filter(isBusy);
  const card = el('section', { class: 'card', 'aria-label': 'Përpunimi' });
  if (!busy.length) {
    card.append(
      cardTitle('wave', 'Asgjë në përpunim', 'yellow'),
      el('p', { class: 'small muted', text: 'Ngarko një regjistrim ose regjistro mbledhjen direkt nga mikrofoni. Sistemi e transkripton dhe i nxjerr vendimet e detyrat automatikisht.' }),
      el('div', { class: 'actions' }, ...newMeetingButtons().reverse()),
    );
    return card;
  }
  const m = busy[0];
  card.append(...nn(
    el('div', { class: 'stack', style: { gap: '6px' } },
      el('span', { class: 'eyebrow accent', text: m.status === 'queued' ? 'Në radhë' : 'Duke përpunuar' }),
      el('a', { href: `#/meeting/${m.id}` }, el('h2', { text: m.title })),
      el('span', { class: 'small subtle', text: [fmtDuration(m.duration_sec), m.language && m.language.toUpperCase(), m.model].filter(Boolean).join(' · ') })),
    el('div', { class: 'progress' }, el('div', { style: { width: `${Math.round((m.status === 'transcribing' ? m.progress : m.status === 'queued' ? 0 : 1) * 100)}%` } })),
    pipelineSteps(m),
    m.note ? el('p', { class: 'small subtle', text: m.note }) : null,
    busy.length > 1 ? el('p', { class: 'small subtle', text: `+${busy.length - 1} të tjera në radhë` }) : null,
  ));
  return card;
}

function recentMeetingsCard(list) {
  const card = el('section', { class: 'card', 'aria-label': 'Mbledhjet e fundit', style: { gap: '4px' } },
    el('div', { class: 'card-head', style: { marginBottom: '8px' } }, cardTitle('mic', 'Mbledhjet e fundit'),
      list.length > 5 ? el('a', { href: '#/meetings', class: 'small', text: 'Shiko të gjitha' }) : null));
  if (!list.length) {
    card.append(el('div', { class: 'empty' }, el('strong', { text: 'Ende s’ka mbledhje' }), 'Shto të parën me “Ngarko audio” ose “Regjistro”.'));
  } else card.append(...list.slice(0, 5).map(meetingRow));
  return card;
}

function openTasksCard(tasks) {
  const card = el('section', { class: 'card', 'aria-label': 'Detyrat e hapura', style: { gap: '4px' } },
    el('div', { class: 'card-head', style: { marginBottom: '4px' } }, cardTitle('check', isManager() ? 'Detyrat e hapura' : 'Detyrat e mia', 'yellow'),
      el('a', { href: '#/tasks', class: 'small', text: tasks.length ? `${tasks.length} gjithsej` : '' })));
  if (!tasks.length) card.append(el('div', { class: 'empty' }, el('strong', { text: 'Asnjë detyrë e hapur' }), 'Detyrat nga mbledhjet shfaqen këtu.'));
  else card.append(el('div', {}, tasks.slice(0, 6).map((t) => taskRow(t, { showMeeting: true }))));
  return card;
}

/* ---------- Meetings ---------- */

async function renderMeetings() {
  const list = await api('/api/meetings');
  const slot = el('section', { class: 'card', style: { gap: '4px' } });
  const paint = (l) => slot.replaceChildren(...(l.length ? l.map(meetingRow)
    : [el('div', { class: 'empty' }, el('strong', { text: 'Ende s’ka mbledhje' }), 'Shto të parën me “Ngarko audio” ose “Regjistro”.')]));
  paint(list);
  view.replaceChildren(el('div', { class: 'page' }, pageHead(`${list.length} gjithsej`, 'Mbledhjet', ...newMeetingButtons()), slot));
  if (list.some(isBusy)) poll(async () => { const l = await api('/api/meetings'); paint(l); if (!l.some(isBusy)) stopPolling(); });
}

/* ---------- Tasks ---------- */

async function renderTasks() {
  const [all] = await Promise.all([api('/api/tasks'), loadOrg()]);
  let filter = 'open';
  let person = '';
  const listSlot = el('section', { class: 'card', style: { gap: 0 } });
  const filterBar = el('div', { class: 'filter', role: 'group', 'aria-label': 'Filtro detyrat' });
  const ownerOk = (t) => !person || (person === 'none' ? !t.assignee_id : t.assignee_id === Number(person));
  const personSel = el('select', { class: 'assign', 'aria-label': 'Filtro sipas personit', style: { minHeight: '44px', maxWidth: '260px' },
    onchange: () => { person = personSel.value; paint(); } },
  el('option', { value: '', text: 'Të gjithë personat' }),
  el('option', { value: 'none', text: 'Pa lidhje me punonjës' }),
  state.org.employees.map((e) => el('option', { value: e.id, text: e.full_name })));
  const paint = () => {
    const mine = all.filter(ownerOk);
    const shown = mine.filter((t) => filter === 'all' || t.status === filter);
    filterBar.replaceChildren(...[['open', 'Të hapura'], ['done', 'Të kryera'], ['all', 'Të gjitha']].map(([k, label]) => el('button', {
      type: 'button', class: filter === k ? 'active' : '', 'aria-pressed': String(filter === k),
      text: `${label} ${mine.filter((t) => k === 'all' || t.status === k).length}`,
      onclick: () => { filter = k; paint(); },
    })));
    listSlot.replaceChildren(...(shown.length ? shown.map((t) => taskRow(t, { showMeeting: true, onChange: paint, assignable: isManager() }))
      : [el('div', { class: 'empty' }, el('strong', { text: filter === 'done' ? 'Asnjë detyrë e kryer ende' : 'Asnjë detyrë këtu' }),
        'Detyrat krijohen automatikisht nga mbledhjet.')]));
  };
  paint();
  view.replaceChildren(el('div', { class: 'page' },
    pageHead(isManager() ? 'Nga mbledhjet e projekteve të tua' : 'Të caktuara ty', isManager() ? 'Detyrat' : 'Detyrat e mia',
      isManager() ? personSel : null, filterBar), listSlot));
}

/* ---------- Ask ---------- */

function renderAsk() {
  const chat = el('div', { class: 'chat' });
  const ask = askBox({
    autofocus: true,
    onAsk: (q, input, send) => {
      const out = el('div');
      chat.append(el('div', { class: 'chat-q', text: q }), out);
      input.value = '';
      runAsk(q, input, send, out).then(() => { out.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); input.focus(); });
    },
  });
  view.replaceChildren(el('div', { class: 'page', style: { maxWidth: '860px' } },
    pageHead('Kujtesa e ekipit', 'Pyet arkivin'),
    keyNotice(),
    el('p', { class: 'muted', text: 'Sistemi kërkon në transkriptet, vendimet dhe detyrat e të gjitha mbledhjeve, dhe përgjigjet me burimin e saktë.' }),
    el('section', { class: 'card' }, ask.form, ask.chips),
    chat));
}

/* ---------- Meeting detail ---------- */

async function renderMeeting(id) {
  stopPolling();
  if (state.cleanup) { state.cleanup(); state.cleanup = null; }
  const [m] = await Promise.all([api(`/api/meetings/${id}`), loadOrg()]);
  const startAt = Number(new URLSearchParams(location.hash.split('?')[1] || '').get('t')) || null;

  const actions = [];
  const unsent = (m.tasks || []).filter((t) => !t.jira_key && t.status !== 'done');
  const ready = unsent.filter((t) => t.approved_at);
  const drafts = unsent.filter((t) => !t.approved_at);
  const pending = ready.length;
  if (m.can_manage && unsent.length && ['done', 'needs_key'].includes(m.status)) {
    actions.push(el('button', {
      type: 'button', class: 'btn btn-primary', disabled: !pending,
      title: !pending ? 'Konfirmo së paku një detyrë para se ta dërgosh në Jira'
        : state.status.jira ? '' : 'Jira s’është konfiguruar ende në .env',
      onclick: async (e) => {
        const list = ready.map((t) => `• ${t.description}${t.assignee_name ? ` (${t.assignee_name})` : ''}${t.due_date ? ` · ${t.due_date}` : ''}`).join('\n');
        const note = drafts.length ? `\n\n${drafts.length} detyra draft NUK dërgohen (s’janë konfirmuar).` : '';
        if (!confirm(`T’i krijoj këto ${pending} detyra të konfirmuara si tiketa në Jira?\n\n${list}${note}`)) return;
        e.currentTarget.disabled = true;
        try {
          const r = await api(`/api/meetings/${m.id}/jira`, { method: 'POST', body: JSON.stringify({ task_ids: ready.map((t) => t.id) }) });
          toast(r.created.length ? `U krijuan ${r.created.length} tiketa: ${r.created.join(', ')}${r.failed ? ` · ${r.failed} dështuan` : ''}`
            : `Asnjë tiketë s’u krijua${r.failed ? ` (${r.failed} dështuan)` : ''}.`);
        } catch (err) { toast(err.message); }
        renderMeeting(m.id);
      },
    }, icon('jira', 16, 2), `Dërgo në Jira (${pending})`));
  }
  if (m.can_manage && !isBusy(m) && m.transcript && state.status.claude) {
    actions.push(el('button', { type: 'button', class: 'btn', onclick: () => {
      if (m.tasks.length && !confirm('Analiza e re i zëvendëson vendimet dhe detyrat, edhe ndryshimet dhe konfirmimet e tua. Vazhdo?')) return;
      retry(m.id);
    } }, icon('retry', 16, 2), 'Analizo përsëri'));
  }
  if (m.can_manage && !['transcribing', 'diarizing', 'extracting'].includes(m.status)) {
    actions.push(el('button', {
      type: 'button', class: 'btn btn-danger', onclick: async () => {
        if (!confirm(`Ta fshij mbledhjen “${m.title}” bashkë me detyrat dhe audion?`)) return;
        try { await api(`/api/meetings/${m.id}`, { method: 'DELETE' }); toast('Mbledhja u fshi.'); location.hash = '#/meetings'; } catch (e) { toast(e.message); }
      },
    }, icon('trash', 16, 2), 'Fshi'));
  }

  const lines = m.transcript ? (m.transcript.utterances || m.transcript.segments) : [];
  const colors = speakerColors(lines);
  const meta = el('div', { class: 'meta-line' },
    el('span', { text: fmtLong(m.meeting_date) }),
    m.duration_sec ? [el('span', { class: 'sep', text: '/' }), el('span', { text: fmtDuration(m.duration_sec) })] : null,
    m.language ? el('span', { class: 'tag', text: m.language }) : null,
    colors.size ? el('span', { text: `${colors.size} folës` }) : null,
    m.team_name ? el('span', { class: 'tag-team', text: m.team_name }) : null,
    m.project_name ? el('span', { class: 'tag-project', text: m.project_name }) : null,
    statusChip(m));

  const page = el('div', { class: 'page' },
    el('div', { class: 'card-head' }, el('a', { class: 'back', href: '#/' }, icon('back', 18, 2), 'Paneli'), el('div', { class: 'actions' }, ...actions)),
    el('header', { class: 'stack', style: { gap: '12px' } }, el('h1', { text: m.title, style: { fontSize: '36px' } }), meta));

  if (isBusy(m)) {
    page.append(el('section', { class: 'card', style: { maxWidth: '560px' } },
      el('span', { class: 'eyebrow accent', text: 'Duke përpunuar' }),
      el('div', { class: 'progress' }, el('div', { style: { width: `${Math.round((m.status === 'transcribing' ? m.progress : m.status === 'queued' ? 0 : 1) * 100)}%` } })),
      pipelineSteps(m),
      el('p', { class: 'small subtle', text: m.note || 'Mund ta mbyllësh këtë faqe; përpunimi vazhdon në sfond.' })));
    view.replaceChildren(page);
    let last = `${m.status}:${Math.round(m.progress * 100)}`;
    poll(async () => {
      const now = await api(`/api/meetings/${id}`);
      const key = `${now.status}:${Math.round(now.progress * 100)}`;
      if (key !== last) { last = key; stopPolling(); renderMeeting(id); }
    }, 2000);
    return;
  }

  if (m.status === 'error') {
    page.append(notice('error', 'alert', el('strong', { text: 'Përpunimi dështoi. ' }), m.error || '',
      m.can_manage ? el('div', { style: { marginTop: '10px' } }, el('button', { type: 'button', class: 'btn btn-sm', onclick: () => retry(m.id) }, 'Provo përsëri')) : null));
  }
  if (m.status === 'needs_key') {
    page.append(notice('warn', 'key', m.note || 'Transkripti është gati.',
      state.status.claude && m.can_manage ? el('div', { style: { marginTop: '10px' } },
        el('button', { type: 'button', class: 'btn btn-primary btn-sm', onclick: () => retry(m.id) }, 'Nxirr vendimet dhe detyrat')) : null));
  } else if (m.note && m.status === 'done') {
    page.append(notice('', 'alert', m.note));
  }

  const player = m.source !== 'json' && m.transcript ? buildPlayer(m, lines, startAt) : null;
  if (player) page.append(player.node);

  const transcriptCard = el('section', { class: 'card', 'aria-label': 'Transkripti' },
    el('div', { class: 'card-head' }, el('h2', { text: 'Transkripti' }),
      colors.size ? el('div', { class: 'speakers' }, [...colors].map(([name, c]) => el('span', { class: 'speaker' },
        el('span', { class: 'dot', style: { background: c } }), name))) : null));
  const lineNodes = [];
  if (!lines.length) transcriptCard.append(el('div', { class: 'empty', text: 'S’ka transkript.' }));
  else {
    const box = el('div', { class: 'transcript' });
    for (const l of lines) {
      const node = el('button', {
        type: 'button', class: 'line', onclick: () => player && player.seek(l.start),
        title: player ? 'Dëgjo nga ky moment' : null,
      },
      el('span', { class: 't', text: fmtClock(l.start) }),
      el('span', { class: 'body' },
        l.speaker ? el('span', { class: 'who', text: l.speaker, style: { color: colors.get(l.speaker) } }) : null,
        el('span', { class: 'txt', text: l.text })));
      lineNodes.push([l.start, node]);
      box.append(node);
    }
    transcriptCard.append(box);
    if (player) player.onTime((t) => {
      let cur = null;
      for (const [s, n] of lineNodes) { if (s <= t + 0.2) cur = n; else break; }
      lineNodes.forEach(([, n]) => n.classList.toggle('now', n === cur));
      if (cur && player.playing()) {
        const top = cur.offsetTop - box.offsetTop;
        if (top < box.scrollTop || top > box.scrollTop + box.clientHeight - 80) box.scrollTop = top - 40;
      }
    });
  }

  const side = el('div', { class: 'col-2 stack' });
  const tasksCard = el('section', { class: 'card', style: { gap: '4px' } },
        el('div', { class: 'card-head', style: { marginBottom: '4px' } },
          el('span', { class: 'eyebrow', text: 'Detyrat' }),
          el('div', { class: 'actions' },
            m.can_manage && drafts.length ? el('button', { type: 'button', class: 'btn btn-sm', title: 'Konfirmo të gjitha detyrat draft ashtu siç janë', onclick: async (e) => {
              if (!confirm(`T’i konfirmoj të ${drafts.length} detyrat draft pa ndryshime?`)) return;
              e.currentTarget.disabled = true;
              try { const r = await api(`/api/meetings/${m.id}/approve`, { method: 'POST' }); toast(`U konfirmuan ${r.approved} detyra.`); }
              catch (err) { toast(err.message); }
              renderMeeting(m.id);
            } }, icon('tick', 14, 2.4), `Konfirmo të gjitha (${drafts.length})`) : null,
            m.can_manage ? el('button', { type: 'button', class: 'btn btn-sm', onclick: () => taskForm(m.id, null, () => renderMeeting(m.id)) }, icon('plus', 14, 2.4), 'Shto') : null,
            el('span', { class: 'mono small muted', text: m.tasks.length }))),
        m.can_manage && drafts.length ? el('p', { class: 'small subtle', text: 'Detyrat “Draft” i ka nxjerrë AI-ja. Kontrolloji, ndryshoji nëse duhet dhe konfirmoji: vetëm detyrat e konfirmuara mund të shkojnë në Jira.' }) : null,
        m.tasks.length ? el('div', {}, m.tasks.map((t) => taskRow(t, { assignable: m.can_manage, readOnly: !m.can_manage && t.assignee_id !== state.me.id,
          manage: m.can_manage ? { meetingId: m.id, reload: () => renderMeeting(m.id) } : null }))) : el('p', { class: 'small subtle', text: 'Asnjë detyrë.' }));

  if (m.summary !== null && m.summary !== undefined) {
    side.append(...nn(
      el('section', { class: 'card' }, el('span', { class: 'eyebrow', text: 'Përmbledhja' }), el('p', { class: 'summary', text: m.summary })),
      el('section', { class: 'card' },
        el('div', { class: 'card-head' }, el('span', { class: 'eyebrow', text: 'Vendimet' }), el('span', { class: 'mono small muted', text: m.decisions.length })),
        m.decisions.length ? m.decisions.map((d) => el('div', { class: 'decision' }, el('b', { text: d.description }), quote(d.quote, lines, player)))
          : el('p', { class: 'small subtle', text: 'Asnjë vendim i marrë.' })),
      tasksCard,
      m.open_questions.length ? el('section', { class: 'card' }, el('span', { class: 'eyebrow', text: 'Çështje të hapura' }),
        el('div', { class: 'list' }, m.open_questions.map((q) => el('div', { class: 'list-row', text: q })))) : null,
    ));
  } else if (m.transcript) {
    side.append(el('section', { class: 'card' }, el('span', { class: 'eyebrow', text: 'Analiza' }),
      el('div', { class: 'empty' }, el('strong', { text: 'Ende pa përmbledhje' }), 'Vendimet dhe detyrat shfaqen këtu pasi Claude ta analizojë transkriptin.')));
    if (m.can_manage && !isBusy(m)) side.append(tasksCard);  // the PM can still add tasks by hand
  }

  page.append(el('div', { class: 'row' }, el('div', { class: 'col-3 stack' }, transcriptCard), side));
  view.replaceChildren(page);
}

function quote(text, lines, player) {
  if (!text) return null;
  // Find the transcript line the quote came from, so it can be played
  const needle = text.toLowerCase().replace(/[^\p{L}\p{N} ]/gu, '').slice(0, 40);
  const hit = needle.length >= 8 && lines.find((l) => l.text.toLowerCase().replace(/[^\p{L}\p{N} ]/gu, '').includes(needle));
  if (hit && player) {
    return el('button', { type: 'button', class: 'quote', onclick: () => player.seek(hit.start) },
      `„${text}” `, el('span', { class: 'mono', style: { color: 'var(--accent-text)' }, text: fmtClock(hit.start) }));
  }
  return el('span', { class: 'quote', text: `„${text}”${hit ? ` · ${fmtClock(hit.start)}` : ''}` });
}

function buildPlayer(m, lines, startAt) {
  const audio = el('audio', { src: `/api/meetings/${m.id}/audio`, preload: 'metadata' });
  const playBtn = el('button', { type: 'button', class: 'play', 'aria-label': 'Luaj' });
  const cur = el('span', { class: 'time', text: '00:00' });
  const total = el('span', { class: 'time subtle', text: fmtClock(m.duration_sec) });
  const N = 120;
  let seed = m.id * 9301 + 49297;
  const rand = () => ((seed = (seed * 9301 + 49297) % 233280) / 233280);
  const bars = Array.from({ length: N }, (_, i) => el('span', { style: { height: `${Math.round(8 + 28 * Math.abs(Math.sin(i * 0.37 + rand() * 2)) * (0.45 + rand() * 0.55))}px` } }));
  const wave = el('div', { class: 'wave', role: 'slider', tabindex: '0', 'aria-label': 'Pozicioni në regjistrim', 'aria-valuemin': '0', 'aria-valuemax': String(Math.round(m.duration_sec || 0)), 'aria-valuenow': '0' }, bars);
  const listeners = [];

  const dur = () => (Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : (m.duration_sec || 0));
  const setIcon = () => {
    playBtn.replaceChildren(audio.paused
      ? el('span', { style: { display: 'inline-flex' } }, svgFill('M7 4.5v15l13-7.5z'))
      : el('span', { style: { display: 'inline-flex' } }, svgFill('M6 4h4v16H6zM14 4h4v16h-4z')));
    playBtn.setAttribute('aria-label', audio.paused ? 'Luaj' : 'Ndalo');
  };
  const paint = () => {
    const t = audio.currentTime, d = dur();
    cur.textContent = fmtClock(t);
    const on = d ? Math.round((t / d) * N) : 0;
    bars.forEach((b, i) => b.classList.toggle('on', i < on));
    wave.setAttribute('aria-valuenow', String(Math.round(t)));
    listeners.forEach((fn) => fn(t));
  };
  const seek = (t) => { audio.currentTime = Math.max(0, Math.min(t, dur())); audio.play().catch(() => {}); paint(); };

  playBtn.addEventListener('click', () => (audio.paused ? audio.play().catch((e) => toast(`Audio s’u luajt: ${e.message}`)) : audio.pause()));
  audio.addEventListener('play', setIcon);
  audio.addEventListener('pause', setIcon);
  audio.addEventListener('timeupdate', paint);
  audio.addEventListener('error', () => toast('Audio nuk u gjet ose s’mund të luhet në shfletues.'));
  wave.addEventListener('click', (e) => { const r = wave.getBoundingClientRect(); seek(((e.clientX - r.left) / r.width) * dur()); });
  wave.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowRight') { e.preventDefault(); seek(audio.currentTime + 5); }
    if (e.key === 'ArrowLeft') { e.preventDefault(); seek(audio.currentTime - 5); }
  });
  setIcon();
  if (startAt !== null) audio.addEventListener('loadedmetadata', () => { audio.currentTime = startAt; paint(); }, { once: true });
  state.cleanup = () => { audio.pause(); audio.removeAttribute('src'); audio.load(); };

  return {
    node: el('section', { class: 'player', 'aria-label': 'Regjistrimi' }, playBtn, cur, wave, total, audio),
    seek,
    onTime: (fn) => listeners.push(fn),
    playing: () => !audio.paused,
  };
}

function svgFill(d) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('width', '16'); svg.setAttribute('height', '16'); svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('fill', 'currentColor'); svg.setAttribute('aria-hidden', 'true');
  const p = document.createElementNS(ns, 'path'); p.setAttribute('d', d); svg.append(p);
  return svg;
}

async function retry(id) {
  try { await api(`/api/meetings/${id}/retry`, { method: 'POST' }); toast('U fut në radhë për përpunim.'); renderMeeting(id); } catch (e) { toast(e.message); }
}

/* ================= Team: employees, teams, projects ================= */

const formDlg = document.getElementById('form-dialog');
const formEl = document.getElementById('form-dialog-form');

/** Generic form dialog. fields: nodes; onSave(): Promise; onDelete(): Promise | null. */
function openForm(title, fields, { onSave, onDelete, saveText = 'Ruaj' }) {
  const err = el('p', { class: 'form-error', role: 'alert' });
  const save = el('button', { type: 'submit', class: 'btn btn-primary', text: saveText });
  formEl.replaceChildren(
    el('div', { class: 'dialog-head' }, el('h2', { text: title }),
      el('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Mbyll', onclick: () => formDlg.close() }, icon('x'))),
    ...fields, err,
    el('div', { class: 'dialog-actions' },
      onDelete ? el('button', { type: 'button', class: 'btn btn-danger', onclick: async () => {
        try { if ((await onDelete()) !== false) formDlg.close(); } catch (e) { err.textContent = e.message; }
      } }, icon('trash', 16, 2), 'Fshi') : null,
      el('span', { class: 'spacer' }),
      el('button', { type: 'button', class: 'btn', text: 'Anulo', onclick: () => formDlg.close() }),
      save));
  formEl.onsubmit = async (e) => {
    e.preventDefault();
    err.textContent = '';
    save.disabled = true;
    try { await onSave(); formDlg.close(); } catch (ex) { err.textContent = ex.message; } finally { save.disabled = false; }
  };
  formDlg.showModal();
  const first = formEl.querySelector('input, textarea, select');
  if (first) first.focus();
}

function field(label, input, { wide = true, hint } = {}) {
  // A checkbox list can't sit inside <label> (clicking the title would toggle the first box)
  const group = input.tagName === 'DIV';
  if (group) { input.setAttribute('role', 'group'); input.setAttribute('aria-label', label); }
  return el(group ? 'div' : 'label', { class: `field${wide ? ' field-wide' : ''}` }, label, input, hint ? el('small', { text: hint }) : null);
}

function checklist(items, selected, emptyText) {
  const box = el('div', { class: 'checklist' });
  if (!items.length) box.append(el('div', { class: 'none', text: emptyText }));
  for (const it of items) {
    box.append(el('label', {}, el('input', { type: 'checkbox', value: it.id, checked: selected.includes(it.id) }),
      it.label, it.meta ? el('small', { text: it.meta }) : null));
  }
  box.values = () => [...box.querySelectorAll('input:checked')].map((i) => Number(i.value));
  return box;
}

/** Teams with their projects nested inside; a project can only be checked together with its team. */
function groupTree(teamIds, projectIds) {
  const box = el('div', { class: 'checklist tree' });
  const { teams, projects } = state.org;
  const orphans = projects.filter((p) => !p.team_id || !byId(teams, p.team_id));
  if (!teams.length && !orphans.length) box.append(el('div', { class: 'none', text: 'Ende s’ka ekipe. Krijoji te karta “Ekipet dhe projektet”.' }));
  const teamBoxes = new Map();
  const projectBoxes = [];
  for (const t of teams) {
    const tb = el('input', { type: 'checkbox', checked: teamIds.includes(t.id) });
    teamBoxes.set(t.id, tb);
    box.append(el('label', { class: 'tree-team' }, tb, el('strong', { text: t.name })));
    const kids = projects.filter((p) => p.team_id === t.id);
    for (const p of kids) {
      const pb = el('input', { type: 'checkbox', checked: projectIds.includes(p.id) });
      projectBoxes.push([p, pb]);
      pb.addEventListener('change', () => { if (pb.checked) tb.checked = true; });
      box.append(el('label', { class: 'tree-project' }, pb, p.name));
    }
    tb.addEventListener('change', () => {
      if (!tb.checked) projectBoxes.filter(([p]) => p.team_id === t.id).forEach(([, pb]) => { pb.checked = false; });
    });
  }
  if (orphans.length) {
    box.append(el('div', { class: 'tree-label', text: 'Projekte pa ekip' }));
    for (const p of orphans) {
      const pb = el('input', { type: 'checkbox', checked: projectIds.includes(p.id) });
      projectBoxes.push([p, pb]);
      box.append(el('label', { class: 'tree-project' }, pb, p.name));
    }
  }
  box.teamIds = () => [...teamBoxes].filter(([, b]) => b.checked).map(([id]) => id);
  box.projectIds = () => projectBoxes.filter(([, b]) => b.checked).map(([p]) => p.id);
  return box;
}

function employeeForm(emp, reload) {
  const e = emp || { full_name: '', email: '', role: '', aliases: '', active: true, team_ids: [], project_ids: [] };
  const name = el('input', { type: 'text', value: e.full_name, required: true, maxlength: '200', placeholder: 'Emri Mbiemri' });
  const email = el('input', { type: 'email', value: e.email || '', maxlength: '200', placeholder: 'emri@kompania.com' });
  const role = el('input', { type: 'text', value: e.role || '', maxlength: '200', placeholder: 'p.sh. Zhvillues backend' });
  const aliases = el('input', { type: 'text', value: e.aliases || '', maxlength: '300', placeholder: 'p.sh. Ari, A.K.' });
  const active = el('input', { type: 'checkbox', checked: e.active });
  const groups = groupTree(e.team_ids, e.project_ids);
  openForm(emp ? 'Ndrysho punonjësin' : 'Punonjës i ri', [
    el('div', { class: 'field-grid' },
      field('Emri i plotë', name),
      field('Email', email, { wide: false }),
      field('Roli', role, { wide: false }),
      field('Nofkat', aliases, { hint: 'Si e thërrasin në mbledhje, të ndara me presje. Ndihmon që detyrat t’i caktohen personit të duhur.' }),
      field('Ekipet dhe projektet', groups, { hint: 'Zgjedhja e një projekti e shton automatikisht edhe në ekipin e tij.' }),
      el('label', { class: 'check-inline field-wide' }, active, 'Aktiv (merr pjesë në mbledhje)'),
      ),
  ], {
    onSave: async () => {
      const body = JSON.stringify({ full_name: name.value, email: email.value, role: role.value, aliases: aliases.value,
        active: active.checked, team_ids: groups.teamIds(), project_ids: groups.projectIds() });
      await api(emp ? `/api/employees/${emp.id}` : '/api/employees', { method: emp ? 'PUT' : 'POST', body });
      toast(emp ? 'Ndryshimet u ruajtën.' : 'Punonjësi u shtua.');
      reload();
    },
    onDelete: emp ? async () => {
      if (!confirm(`Ta fshij “${emp.full_name}”? Detyrat mbesin, por pa lidhje me punonjësin.`)) return false;
      await api(`/api/employees/${emp.id}`, { method: 'DELETE' });
      toast('Punonjësi u fshi.');
      reload();
    } : null,
  });
}

/** kind = 'team' | 'project'. For a new project, `presetTeamId` picks its team. */
function groupForm(kind, group, reload, presetTeamId = null) {
  const g = group || { name: '', description: '', team_id: presetTeamId, member_ids: [] };
  const word = kind === 'team' ? 'ekip' : 'projekt';
  const name = el('input', { type: 'text', value: g.name, required: true, maxlength: '120' });
  const desc = el('input', { type: 'text', value: g.description || '', maxlength: '500' });
  const people = (ids) => state.org.employees.filter((e) => e.active || g.member_ids.includes(e.id))
    .filter((e) => !ids || ids.includes(e.id))
    .map((e) => ({ id: e.id, label: e.full_name, meta: e.role }));

  let members;
  let membersSlot = el('div', { class: 'field field-wide' });
  const pmSel = kind === 'project' ? el('select', {}) : null;
  const jiraKey = kind === 'project' ? el('input', { type: 'text', value: g.jira_key || '', maxlength: '10', placeholder: 'p.sh. PAY',
    style: { textTransform: 'uppercase' } }) : null;
  const paintMembers = () => {
    if (kind === 'team') {
      members = checklist(people(null), g.member_ids, 'Shto fillimisht punonjësit.');
    } else {
      const t = byId(state.org.teams, Number(team.value));
      const keep = members ? members.values() : g.member_ids;
      members = checklist(t ? people(t.member_ids) : [], keep,
        t ? `Ekipi “${t.name}” s’ka ende anëtarë. Shtoji te ekipi.` : 'Zgjidh fillimisht ekipin.');
    }
    if (pmSel) {
      // The PM is one person from the project's team
      const t = byId(state.org.teams, Number(team.value));
      const keepPm = pmSel.value || (g.pm_id ? String(g.pm_id) : '');
      pmSel.replaceChildren(el('option', { value: '', text: t ? 'Pa PM' : 'Zgjidh fillimisht ekipin' }),
        (t ? people(t.member_ids) : []).map((p) => el('option', { value: p.id, text: p.label, selected: String(p.id) === keepPm })));
    }
    membersSlot.replaceWith(membersSlot = field('Anëtarët', members, {
      hint: kind === 'team'
        ? 'Kush hiqet nga ekipi, hiqet edhe nga projektet e tij.'
        : 'Vetëm anëtarët e ekipit mund të jenë në projekt. Claude i njeh këta emra kur mbledhja lidhet me projektin.',
    }));
  };

  const team = kind === 'project' ? el('select', { required: true, onchange: paintMembers },
    el('option', { value: '', text: 'Zgjidh ekipin…' }),
    state.org.teams.map((t) => el('option', { value: t.id, text: t.name, selected: t.id === g.team_id }))) : null;

  const grid = el('div', { class: 'field-grid' },
    field('Emri', name, { wide: kind === 'team' }),
    team ? field('Ekipi', team, { wide: false }) : null,
    pmSel ? field('Project manager', pmSel, { wide: false, hint: 'Merr me email përmbledhjet e mbledhjeve dhe përkujtuesit.' }) : null,
    jiraKey ? field('Çelësi në Jira', jiraKey, { wide: false, hint: 'Ku krijohen tiketat e këtij projekti.' }) : null,
    field('Përshkrimi', desc),
    membersSlot);
  paintMembers();
  openForm(group ? `Ndrysho ${word}in` : (kind === 'team' ? 'Ekip i ri' : 'Projekt i ri'), [grid], {
    onSave: async () => {
      const body = JSON.stringify({ name: name.value, description: desc.value, team_id: team && team.value ? Number(team.value) : null,
        pm_id: pmSel && pmSel.value ? Number(pmSel.value) : null, jira_key: jiraKey ? jiraKey.value : null, member_ids: members.values() });
      await api(group ? `/api/${kind}s/${group.id}` : `/api/${kind}s`, { method: group ? 'PUT' : 'POST', body });
      toast('U ruajt.');
      reload();
    },
    onDelete: group ? async () => {
      if (!confirm(`Ta fshij ${word}in “${group.name}”? Punonjësit nuk fshihen.`)) return false;
      await api(`/api/${kind}s/${group.id}`, { method: 'DELETE' });
      toast('U fshi.');
      reload();
    } : null,
  });
}

function importForm(reload) {
  const text = el('textarea', { placeholder: 'Emri, Email, Roli, Ekipet, Projektet\nEmri Mbiemri, emri@kompania.com, Zhvillues, Backend, Projekti A; Projekti B', spellcheck: false });
  openForm('Importo punonjësit', [
    el('p', { class: 'small muted', text: 'Një punonjës për rresht: Emri, Email, Roli, Ekipet, Projektet. Disa ekipe ose projekte ndahen me pikëpresje (;). Ekipet dhe projektet që s’ekzistojnë krijohen automatikisht. Nëse emaili ose emri ekziston, punonjësi përditësohet, nuk dyfishohet.' }),
    field('Lista (CSV)', text, { hint: 'Mund ta kopjosh direkt nga Excel pasi ta ruash si CSV.' }),
  ], {
    saveText: 'Importo',
    onSave: async () => {
      if (!text.value.trim()) throw new Error('Lista është bosh.');
      const r = await api('/api/employees/import', { method: 'POST', body: JSON.stringify({ csv: text.value }) });
      toast(`${r.created} të rinj, ${r.updated} të përditësuar${r.errors.length ? `, ${r.errors.length} gabime` : ''}.`);
      reload();
    },
  });
}

async function renderTeam() {
  await loadOrg();
  let filter = { type: 'all', id: null };
  let query = '';
  const reload = async () => { await loadOrg(); paint(); };

  const listCard = el('section', { class: 'card', style: { gap: '4px' } });
  const filterChips = el('div', { class: 'chips' });
  const search = el('input', { type: 'search', class: 'search-input', placeholder: 'Kërko sipas emrit, rolit ose emailit…', 'aria-label': 'Kërko punonjës',
    oninput: () => { query = search.value.toLowerCase(); paintList(); } });
  const orgCard = el('section', { class: 'card', style: { gap: '4px' }, 'aria-label': 'Ekipet dhe projektet' });

  const visible = () => state.org.employees.filter((e) => {
    if (filter.type === 'team' && !e.team_ids.includes(filter.id)) return false;
    if (filter.type === 'project' && !e.project_ids.includes(filter.id)) return false;
    if (filter.type === 'none' && (e.team_ids.length || e.project_ids.length)) return false;
    return !query || [e.full_name, e.role, e.email, e.aliases].some((x) => (x || '').toLowerCase().includes(query));
  });

  const chip = (label, f) => el('button', {
    type: 'button', class: `chip${filter.type === f.type && filter.id === f.id ? ' active' : ''}`, text: label,
    'aria-pressed': String(filter.type === f.type && filter.id === f.id), onclick: () => { filter = f; paint(); },
  });

  const paintList = () => {
    const people = visible();
    const { teams, projects } = state.org;
    listCard.replaceChildren(
      el('div', { class: 'card-head', style: { marginBottom: '8px' } },
        el('h2', { text: 'Punonjësit' }), el('span', { class: 'mono small muted', text: `${people.length} / ${state.org.employees.length}` })),
      ...(people.length ? people.map((e) => el('div', { class: `person${e.active ? '' : ' inactive'}` },
        el('span', { class: 'avatar', style: { background: personColor(e.id) }, text: initials(e.full_name) }),
        el('div', { class: 'grow' },
          el('span', { class: 'name', text: e.full_name + (e.active ? '' : ' (joaktiv)') + (e.is_admin ? ' · PM' : '')
            + (state.org.projects.some((p) => p.pm_id === e.id) ? ' · PM' : '') }),
          el('span', { class: 'small subtle', text: [e.role, e.email].filter(Boolean).join(' · ') || '—' }),
          (e.team_ids.length || e.project_ids.length) ? el('div', { class: 'tags' },
            // each team followed by this person's projects in it
            e.team_ids.map((id) => byId(teams, id)).filter(Boolean).map((t) => [
              el('span', { class: 'tag-team', text: t.name }),
              e.project_ids.map((id) => byId(projects, id)).filter((p) => p && p.team_id === t.id)
                .map((p) => el('span', { class: 'tag-project', text: p.name }))]),
            e.project_ids.map((id) => byId(projects, id)).filter((p) => p && !e.team_ids.includes(p.team_id))
              .map((p) => el('span', { class: 'tag-project', text: p.name }))) : null),
        e.open_tasks ? el('a', { class: 'pill soon', href: '#/tasks', text: `${e.open_tasks} detyra`, title: 'Detyra të hapura' }) : null,
        el('button', { type: 'button', class: 'icon-btn', 'aria-label': `Ndrysho ${e.full_name}`, onclick: () => employeeForm(e, reload) }, icon('edit', 18))))
        : [el('div', { class: 'empty' },
          el('strong', { text: state.org.employees.length ? 'Asnjë punonjës për këtë filtër' : 'Ende s’ka punonjës' }),
          state.org.employees.length ? 'Ndrysho filtrin ose kërkimin.' : 'Shtoji një nga një, ose importo listën e plotë me CSV.')]),
    );
  };

  const faces = (ids) => el('span', { class: 'faces' }, ids.slice(0, 4).map((id) => {
    const e = byId(state.org.employees, id);
    return e ? el('span', { class: 'face', title: e.full_name, style: { background: personColor(e.id) }, text: initials(e.full_name) }) : null;
  }), ids.length > 4 ? el('span', { class: 'face more', text: `+${ids.length - 4}` }) : null);

  const selectable = (f, ...children) => el('button', {
    type: 'button', class: `org-pick${filter.type === f.type && filter.id === f.id ? ' active' : ''}`,
    'aria-pressed': String(filter.type === f.type && filter.id === f.id),
    onclick: () => { filter = filter.type === f.type && filter.id === f.id ? { type: 'all', id: null } : f; paint(); },
  }, ...children);

  const paintOrg = () => {
    const { teams, projects } = state.org;
    const orphans = projects.filter((p) => !p.team_id || !byId(teams, p.team_id));
    const projectRow = (p) => el('div', { class: 'org-project' },
      selectable({ type: 'project', id: p.id },
        el('span', { class: 'org-name', text: p.name + (p.jira_key ? ` · ${p.jira_key}` : '') }),
        el('span', { class: 'small subtle', text: [p.pm_id && byId(state.org.employees, p.pm_id) ? `PM: ${byId(state.org.employees, p.pm_id).full_name}` : 'pa PM',
          `${p.member_ids.length} anëtarë`].join(' · ') })),
      faces(p.member_ids),
      el('button', { type: 'button', class: 'icon-btn', 'aria-label': `Ndrysho projektin ${p.name}`, onclick: () => groupForm('project', p, reload) }, icon('edit', 16)));
    orgCard.replaceChildren(...nn(
      el('div', { class: 'card-head', style: { marginBottom: '8px' } }, cardTitle('users', 'Ekipet dhe projektet'),
        el('button', { type: 'button', class: 'btn btn-sm', onclick: () => groupForm('team', null, reload) }, icon('plus', 14, 2.2), 'Ekip')),
      teams.length ? null : el('div', { class: 'empty' }, el('strong', { text: 'Ende s’ka ekipe' }), 'Krijo një ekip, pastaj shto projektet e tij.'),
      teams.map((t) => {
        const kids = projects.filter((p) => p.team_id === t.id);
        return el('div', { class: 'org-team' },
          el('div', { class: 'org-team-head' },
            selectable({ type: 'team', id: t.id },
              el('span', { class: 'org-name', text: t.name }),
              el('span', { class: 'small subtle', text: [`${t.member_ids.length} anëtarë`, `${kids.length} projekte`, t.description].filter(Boolean).join(' · ') })),
            faces(t.member_ids),
            el('button', { type: 'button', class: 'icon-btn', 'aria-label': `Ndrysho ekipin ${t.name}`, onclick: () => groupForm('team', t, reload) }, icon('edit', 16))),
          el('div', { class: 'org-projects' },
            kids.map(projectRow),
            el('button', { type: 'button', class: 'org-add', onclick: () => groupForm('project', null, reload, t.id) }, icon('plus', 14, 2.2), 'Shto projekt')));
      }),
      orphans.length ? el('div', { class: 'org-team' },
        el('div', { class: 'org-team-head' }, el('span', { class: 'org-name subtle', text: 'Projekte pa ekip' })),
        el('p', { class: 'small subtle', text: 'Hapi dhe zgjidh ekipin të cilit i përkasin.' }),
        el('div', { class: 'org-projects' }, orphans.map(projectRow))) : null,
    ));
  };

  const paint = () => {
    const { teams, projects, employees } = state.org;
    const activeTeam = filter.type === 'team' ? filter.id : filter.type === 'project' ? (byId(projects, filter.id) || {}).team_id : null;
    filterChips.replaceChildren(...nn(
      chip(`Të gjithë ${employees.length}`, { type: 'all', id: null }),
      teams.map((t) => [
        chip(`${t.name} ${t.member_ids.length}`, { type: 'team', id: t.id }),
        // a team's projects show up once the team is selected
        t.id === activeTeam ? projects.filter((p) => p.team_id === t.id)
          .map((p) => chip(`↳ ${p.name} ${p.member_ids.length}`, { type: 'project', id: p.id })) : null,
      ]),
      employees.some((e) => !e.team_ids.length && !e.project_ids.length) ? chip('Pa ekip', { type: 'none', id: null }) : null));
    paintList();
    paintOrg();
  };

  paint();
  view.replaceChildren(el('div', { class: 'page' },
    pageHead('Organizata', 'Ekipi',
      el('button', { type: 'button', class: 'btn', onclick: () => importForm(reload) }, icon('file', 16, 2), 'Importo CSV'),
      el('button', { type: 'button', class: 'btn btn-primary', onclick: () => employeeForm(null, reload) }, icon('plus', 16, 2.2), 'Shto punonjës')),
    el('p', { class: 'muted', text: 'Çdo projekt i përket një ekipi dhe anëtarët e tij janë nga ai ekip. Kur një mbledhje lidhet me ekipin ose projektin, Claude i njeh emrat dhe detyrat u caktohen personave të vërtetë. Të dhënat ruhen vetëm në këtë kompjuter.' }),
    el('div', { class: 'card', style: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: '12px', padding: '16px' } }, search, filterChips),
    el('div', { class: 'row' }, el('div', { class: 'col-3 stack' }, listCard), el('div', { class: 'col-2 stack' }, orgCard))));
}

/* ================= Outbox (emails) ================= */

async function renderOutbox() {
  const list = await api('/api/outbox');
  const STATUS_EMAIL = { sent: ['Dërguar', 'done'], failed: ['Dështoi', 'error'], 'dry-run': ['Test, pa u dërguar', 'warn'] };
  const KIND = { login: 'Kod hyrjeje', meeting: 'Përmbledhje mbledhjeje', reminder: 'Përkujtues' };
  const card = el('section', { class: 'card', style: { gap: 0 } },
    list.length ? list.map((o) => el('div', { class: 'outbox-row' },
      el('span', { class: 'badge' }, icon('mail', 18, 2)),
      el('div', { class: 'grow' },
        el('strong', { text: o.subject, style: { color: 'var(--heading)' } }),
        el('span', { class: 'small subtle', text: `${KIND[o.kind] || o.kind || ''} · te ${o.to_email} · ${o.created_at.replace('T', ' ')}` }),
        o.error ? el('span', { class: 'jira-error', text: o.error }) : null,
        el('details', {}, el('summary', { text: 'Shiko përmbajtjen' }), el('pre', { text: o.body }))),
      el('span', { class: `status ${(STATUS_EMAIL[o.status] || ['', 'work'])[1]}`, text: (STATUS_EMAIL[o.status] || [o.status])[0] })))
      : el('div', { class: 'empty' }, el('strong', { text: 'Asnjë email ende' }), 'Këtu shfaqen kodet e hyrjes, përmbledhjet për PM-të dhe përkujtuesit.'));
  view.replaceChildren(el('div', { class: 'page' },
    pageHead('Emailet e dërguara', 'Njoftimet',
      el('button', { type: 'button', class: 'btn', onclick: async () => {
        try { const r = await api('/api/reminders/run', { method: 'POST' }); toast(r.sent ? `U dërguan ${r.sent} përkujtues.` : 'Asnjë përkujtues për sot (ose janë dërguar tashmë).'); renderOutbox(); }
        catch (e) { toast(e.message); }
      } }, icon('mail', 16, 2), 'Dërgo përkujtuesit e sotëm')),
    state.status.email ? null : notice('warn', 'mail', el('strong', { text: 'Emaili s’është konfiguruar. ' }),
      'Asgjë s’po dërgohet jashtë: emailet ruhen vetëm këtu. Për t’i dërguar, plotëso SMTP_USER dhe SMTP_PASSWORD (App password i Google) në .env dhe rinise serverin.'),
    card));
}

/* ================= Upload / record dialog ================= */

const dlg = document.getElementById('upload-dialog');
const form = document.getElementById('upload-form');
const fileInput = document.getElementById('file-input');
const drop = document.getElementById('drop');
const submitBtn = document.getElementById('upload-submit');
const errBox = document.getElementById('upload-error');
const recBtn = document.getElementById('rec-btn');
const rec = { recorder: null, stream: null, chunks: [], timer: null, started: 0 };
let chosenFile = null;
let mode = 'file';

function setMode(next) {
  mode = next;
  dlg.querySelectorAll('.tab').forEach((t) => { const on = t.dataset.mode === next; t.classList.toggle('active', on); t.setAttribute('aria-selected', String(on)); });
  dlg.querySelectorAll('[data-pane]').forEach((p) => { p.hidden = p.dataset.pane !== next; });
  if (next === 'file') stopRecording(true);
  chosenFile = null;
  updateFileUI();
}

function updateFileUI() {
  const title = document.getElementById('drop-title');
  const sub = document.getElementById('drop-sub');
  if (mode === 'file' && chosenFile) {
    drop.classList.add('has-file');
    title.textContent = chosenFile.name;
    sub.textContent = `${(chosenFile.size / 1048576).toFixed(1)} MB · kliko për ta ndërruar`;
  } else {
    drop.classList.remove('has-file');
    title.textContent = 'Zgjidh ose tërhiq regjistrimin këtu';
    sub.textContent = 'mp3, wav, m4a, webm… ose një transkript .json';
  }
  submitBtn.disabled = !chosenFile;
}

function pickFile(f) {
  if (!f) return;
  chosenFile = f;
  const t = document.getElementById('f-title');
  if (!t.value) t.value = f.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ');
  updateFileUI();
}

/** Team select + project select (projects grouped by team; a project implies its team). */
function fillMeetingGroups() {
  const teamSel = document.getElementById('f-team');
  const projSel = document.getElementById('f-project');
  // A PM adds meetings only to the projects they manage
  const projects = isAdmin() ? state.org.projects : state.org.projects.filter((p) => state.me.pm_project_ids.includes(p.id));
  const teams = isAdmin() ? state.org.teams : state.org.teams.filter((t) => projects.some((p) => p.team_id === t.id));
  projSel.required = !isAdmin();
  const keepTeam = teamSel.value;
  const paintProjects = () => {
    const tid = Number(teamSel.value) || null;
    const keep = projSel.value;
    const groups = teams.filter((t) => !tid || t.id === tid).map((t) => [t, projects.filter((p) => p.team_id === t.id)]).filter(([, ps]) => ps.length);
    projSel.replaceChildren(
      el('option', { value: '', text: !isAdmin() ? 'Zgjidh projektin…' : projects.length ? 'Pa projekt' : 'Shto te faqja “Ekipi”' }),
      groups.map(([t, ps]) => el('optgroup', { label: t.name }, ps.map((p) => el('option', { value: p.id, text: p.name, selected: String(p.id) === keep })))));
  };
  teamSel.replaceChildren(
    el('option', { value: '', text: teams.length ? 'Pa ekip' : 'Shto te faqja “Ekipi”' }),
    teams.map((t) => el('option', { value: t.id, text: t.name, selected: String(t.id) === keepTeam })));
  teamSel.onchange = paintProjects;
  projSel.onchange = () => {
    const p = byId(projects, Number(projSel.value));
    if (p && p.team_id && teamSel.value !== String(p.team_id)) { teamSel.value = String(p.team_id); paintProjects(); projSel.value = String(p.id); }
  };
  paintProjects();
}

function openUpload(startMode = 'file') {
  form.reset();
  errBox.textContent = '';
  document.getElementById('f-date').value = state.today;
  fillMeetingGroups();
  loadOrg().then(fillMeetingGroups).catch(() => {});
  const sel = document.getElementById('f-model');
  sel.replaceChildren(...state.status.models.map((m) => el('option', { value: m, text: m, selected: m === state.status.default_model })));
  document.getElementById('f-model-hint').textContent = state.status.gpu
    ? 'GPU u gjet: large-v3 jep saktësinë më të mirë.'
    : 'Për shqip rekomandohet “large-v3” (më i sakti). Pa GPU është i ngadalshëm: një mbledhje 1-orëshe mund të zgjasë rreth 1–2 orë. “small” është i shpejtë, por gabon shumë në shqip.';
  setMode(startMode);
  dlg.showModal();
}

async function startRecording() {
  try {
    rec.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    errBox.textContent = 'S’u lejua qasja në mikrofon. Lejoje në shfletues dhe provo përsëri.';
    return;
  }
  const type = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg'].find((t) => MediaRecorder.isTypeSupported(t)) || '';
  rec.recorder = new MediaRecorder(rec.stream, type ? { mimeType: type } : undefined);
  rec.chunks = [];
  rec.recorder.ondataavailable = (e) => e.data.size && rec.chunks.push(e.data);
  rec.recorder.onstop = () => {
    rec.stream.getTracks().forEach((t) => t.stop());
    if (rec.discard) { rec.discard = false; return; }
    const ext = (rec.recorder.mimeType || '').includes('ogg') ? 'ogg' : 'webm';
    chosenFile = new File(rec.chunks, `regjistrim-${state.today}.${ext}`, { type: rec.recorder.mimeType });
    document.getElementById('rec-state').textContent = `Regjistrimi u ruajt (${(chosenFile.size / 1048576).toFixed(1)} MB). Shtyp “Fillo përpunimin”.`;
    submitBtn.disabled = false;
  };
  rec.recorder.start(1000);
  rec.started = Date.now();
  recBtn.classList.add('on');
  recBtn.setAttribute('aria-label', 'Ndalo regjistrimin');
  document.getElementById('rec-state').textContent = 'Po regjistrohet… shtyp përsëri për ta ndalur';
  submitBtn.disabled = true;
  rec.timer = setInterval(() => { document.getElementById('rec-time').textContent = fmtClock((Date.now() - rec.started) / 1000); }, 500);
}

function stopRecording(discard = false) {
  clearInterval(rec.timer);
  recBtn.classList.remove('on');
  recBtn.setAttribute('aria-label', 'Fillo regjistrimin');
  if (rec.recorder && rec.recorder.state !== 'inactive') { rec.discard = discard; rec.recorder.stop(); }
  if (discard) {
    document.getElementById('rec-time').textContent = '00:00';
    document.getElementById('rec-state').textContent = 'Shtyp butonin për të filluar regjistrimin nga mikrofoni';
  }
}

recBtn.addEventListener('click', () => (rec.recorder && rec.recorder.state === 'recording' ? stopRecording() : startRecording()));
dlg.querySelectorAll('.tab').forEach((t) => t.addEventListener('click', () => setMode(t.dataset.mode)));
dlg.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => dlg.close()));
dlg.addEventListener('close', () => stopRecording(true));
fileInput.addEventListener('change', () => pickFile(fileInput.files[0]));
['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', (e) => pickFile(e.dataTransfer.files[0]));

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!chosenFile) return;
  errBox.textContent = '';
  submitBtn.disabled = true;
  submitBtn.textContent = 'Po ngarkohet…';
  const fd = new FormData();
  fd.append('file', chosenFile);
  fd.append('title', document.getElementById('f-title').value);
  fd.append('meeting_date', document.getElementById('f-date').value);
  fd.append('language', document.getElementById('f-lang').value);
  fd.append('model', document.getElementById('f-model').value);
  fd.append('source', mode === 'record' ? 'recording' : 'audio');
  for (const [key, id] of [['team_id', 'f-team'], ['project_id', 'f-project']]) {
    const v = document.getElementById(id).value;
    if (v) fd.append(key, v);
  }
  try {
    const { id } = await api('/api/meetings', { method: 'POST', body: fd });
    dlg.close();
    toast('Mbledhja u shtua. Përpunimi filloi.');
    location.hash = `#/meeting/${id}`;
  } catch (err) {
    errBox.textContent = err.message;
    submitBtn.disabled = false;
  } finally {
    submitBtn.textContent = 'Fillo përpunimin';
  }
});

document.querySelector('[data-action="upload"]').addEventListener('click', () => openUpload('file'));

/* ================= Login ================= */

const loginEl = document.getElementById('login');
const shellEl = document.getElementById('shell');
const loginErr = document.getElementById('login-error');
const emailForm = document.getElementById('login-email-form');
const codeForm = document.getElementById('login-code-form');
let loginEmail = '';

function showLogin() {
  stopPolling();
  state.me = null;
  shellEl.hidden = true;
  loginEl.hidden = false;
  emailForm.hidden = false;
  codeForm.hidden = true;
  loginErr.textContent = '';
  document.getElementById('login-title').textContent = 'Hyr me emailin e punës';
  document.getElementById('login-sub').textContent = 'Do të marrësh një kod 6-shifror në email.';
  setTimeout(() => document.getElementById('login-email').focus(), 0);
}

emailForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = emailForm.querySelector('button');
  loginErr.textContent = '';
  btn.disabled = true;
  loginEmail = document.getElementById('login-email').value.trim();
  try {
    const res = await api('/api/auth/request', { method: 'POST', body: JSON.stringify({ email: loginEmail }) });
    emailForm.hidden = true;
    codeForm.hidden = false;
    document.getElementById('login-title').textContent = 'Shkruaj kodin';
    document.getElementById('login-sub').textContent = res && res.dev_code
      ? `Modaliteti test (pa email real): kodi yt është ${res.dev_code}.`
      : `Nëse ${loginEmail} është i regjistruar, kodi u dërgua aty. Vlen 10 minuta.`;
    const code = document.getElementById('login-code');
    code.value = (res && res.dev_code) || '';
    code.focus();
  } catch (err) { loginErr.textContent = err.message; }
  btn.disabled = false;
});

codeForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = codeForm.querySelector('button[type=submit]');
  loginErr.textContent = '';
  btn.disabled = true;
  try {
    await api('/api/auth/verify', { method: 'POST', body: JSON.stringify({ email: loginEmail, code: document.getElementById('login-code').value }) });
    await start();
  } catch (err) { loginErr.textContent = err.message; }
  btn.disabled = false;
});

document.getElementById('login-back').addEventListener('click', showLogin);

document.getElementById('logout').addEventListener('click', async () => {
  try { await api('/api/auth/logout', { method: 'POST' }); } catch { /* ignore */ }
  location.hash = '#/';
  showLogin();
});

/* ================= Boot ================= */

document.querySelectorAll('[data-icon]').forEach((n) => n.replaceWith(icon(n.dataset.icon, 18, n.closest('.brand-mark') ? 2.2 : 1.9)));

async function start() {
  try {
    state.me = await api('/api/auth/me');
  } catch {
    showLogin();
    return;
  }
  loginEl.hidden = true;
  shellEl.hidden = false;
  document.getElementById('logout').hidden = !!state.me.login_disabled;
  // Role-based navigation
  document.querySelectorAll('[data-role="admin"]').forEach((n) => { n.hidden = !isAdmin(); });
  document.querySelectorAll('[data-role="manager"]').forEach((n) => { n.hidden = !isManager(); });
  document.getElementById('me-name').textContent = state.me.full_name;
  document.getElementById('me-role').textContent = ROLE_LABEL[state.me.role] || state.me.role;
  document.getElementById('me-avatar').textContent = initials(state.me.full_name);
  try {
    await Promise.all([loadStatus(), loadOrg()]);
    await route();
  } catch (e) {
    view.replaceChildren(el('div', { class: 'page' }, notice('error', 'alert', `Serveri nuk përgjigjet: ${e.message}`)));
  }
}

start();
