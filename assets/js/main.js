
// Frontend security helpers. Use textContent/safe DOM APIs for new code; this
// sanitizer protects legacy innerHTML templates from executing tenant data.
const NATIVE_INNERHTML_DESCRIPTOR = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');


function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, '&#096;');
}

window.escapeHtml = escapeHtml;
window.escapeAttr = escapeAttr;
window.GTAVCAD_CONTEXT = window.GTAVCAD_CONTEXT || {
  platformName: 'GTAVCAD',
  communityName: '',
  communitySlug: '',
  cadName: '',
  role: '',
  department: '',
  colors: {}
};

function safeText(value) {
  return String(value ?? '');
}

function createSafeElement(tagName, text = '', className = '') {
  const el = document.createElement(tagName);
  if (className) el.className = className;
  el.textContent = safeText(text);
  return el;
}

function safeEvidenceHref(value, { allowInternalDownload = false } = {}) {
  const raw = String(value ?? '').trim();
  if (!raw || /[\u0000-\u001F\u007F]/.test(raw)) return '';
  try {
    const parsed = new URL(raw, window.location.origin);
    if (allowInternalDownload && parsed.origin === window.location.origin && parsed.pathname.startsWith('/api/cad/evidence/attachments/') && parsed.pathname.endsWith('/download')) {
      return `${parsed.pathname}${parsed.search}`;
    }
    if ((parsed.protocol === 'http:' || parsed.protocol === 'https:') && parsed.username === '' && parsed.password === '') {
      return parsed.href;
    }
  } catch (err) {
    return '';
  }
  return '';
}

function evidenceLinkAction(url, label = 'Open Link', className = '') {
  const safeHref = safeEvidenceHref(url);
  if (!safeHref) return 'Unsafe link blocked';
  const classAttr = className ? ` class="${escapeAttr(className)}"` : '';
  return `<a${classAttr} href="${escapeAttr(safeHref)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`;
}

function sanitizeHTML(html) {
  const template = document.createElement('template');
  NATIVE_INNERHTML_DESCRIPTOR.set.call(template, String(html ?? ''));
  const blockedTags = new Set(['script', 'iframe', 'object', 'embed', 'svg', 'math', 'link', 'meta']);
  const walk = (node) => {
    Array.from(node.childNodes).forEach((child) => {
      if (child.nodeType !== Node.ELEMENT_NODE) return;
      const tag = child.tagName.toLowerCase();
      let unsafe = blockedTags.has(tag);
      Array.from(child.attributes).forEach((attr) => {
        const name = attr.name.toLowerCase();
        const value = String(attr.value || '').trim().toLowerCase();
        if ((name === 'href' || name === 'src' || name === 'xlink:href') && value.startsWith('javascript:')) {
          child.removeAttribute(attr.name);
        }
      });
      if (unsafe) {
        child.remove();
      } else {
        walk(child);
      }
    });
  };
  walk(template.content);
  return template.innerHTML;
}

(function installSafeInnerHTML() {
  const descriptor = NATIVE_INNERHTML_DESCRIPTOR;
  if (!descriptor || !descriptor.set || Element.prototype.__gtavcadSafeInnerHTML) return;
  Object.defineProperty(Element.prototype, 'innerHTML', {
    get: descriptor.get,
    set(value) { descriptor.set.call(this, sanitizeHTML(value)); },
    configurable: true,
    enumerable: descriptor.enumerable,
  });
  Element.prototype.__gtavcadSafeInnerHTML = true;
})();


const PLATFORM_CONTEXT = {
  name: 'GTAVCAD',
  domain: 'gtavcad.app',
  tagline: 'Multi-Community RP/CAD Platform',
  cta: 'Create or Join a Community'
};

function getCommunitySlugFromPath() {
  const parts = window.location.pathname.split('/').filter(Boolean);
  return parts[0] === 'c' && parts[1] ? parts[1] : null;
}

const CURRENT_COMMUNITY_SLUG = getCommunitySlugFromPath();

const CAD_ACCESS_ROLES = ['PlatformOwner', 'CommunityOwner', 'CommunityAdmin', 'Owner', 'Admin', 'Police', 'Officer', 'LEO', 'Dispatch', 'Dispatcher', 'EMS', 'DOJ', 'Staff'];
const CAD_ADMIN_BYPASS_ROLES = ['PlatformOwner', 'CommunityOwner', 'CommunityAdmin', 'Owner', 'Admin'];
const COMMUNITY_ADMIN_ACCESS_ROLES = ['PlatformOwner', 'CommunityOwner', 'CommunityAdmin', 'Owner', 'Admin'];

function normalizeRole(role) {
  return String(role || '').trim();
}

function canAccessOfficerCad() {
  if (window.GTAVCAD_CURRENT_USER?.can_access_police_cad === true) return true;
  if (window.GTAVCAD_CONTEXT?.can_access_police_cad === true) return true;

  // Legacy fallback only when the server-authoritative flag is unavailable.
  const userFlagAvailable = window.GTAVCAD_CURRENT_USER && Object.prototype.hasOwnProperty.call(window.GTAVCAD_CURRENT_USER, 'can_access_police_cad');
  const contextFlagAvailable = window.GTAVCAD_CONTEXT && Object.prototype.hasOwnProperty.call(window.GTAVCAD_CONTEXT, 'can_access_police_cad');
  if (userFlagAvailable || contextFlagAvailable) return false;

  const role = normalizeRole(window.GTAVCAD_CONTEXT?.role || window.GTAVCAD_CURRENT_USER?.role);
  return CAD_ACCESS_ROLES.includes(role);
}

function isCadAdminBypass() {
  return CAD_ADMIN_BYPASS_ROLES.includes(normalizeRole(window.GTAVCAD_CONTEXT?.role));
}

function isOfficerCadPage() {
  const leaf = window.location.pathname.split('/').pop().toLowerCase();
  return leaf === 'police.html' || leaf === 'police' || leaf === 'cad.html' || leaf === 'cad';
}


function removePublicInviteContext() {
  document.querySelectorAll('[data-context-invite]').forEach((el) => {
    const wrapper = el.closest('[data-context-invite-wrap]') || el.closest('span') || el;
    wrapper.remove();
  });
}

function enforceCadRoleVisibility() {
  if (!isOfficerCadPage() || !document.body || document.body.dataset.platformPage === 'true') return true;
  const allowed = canAccessOfficerCad();
  document.body.classList.toggle('cad-police-access', allowed);
  document.body.classList.toggle('cad-admin-bypass', isCadAdminBypass());
  if (allowed) return true;
  const overlay = document.getElementById('officer-login-overlay');
  if (overlay) overlay.style.display = 'none';
  const main = document.querySelector('main');
  if (main) {
    main.innerHTML = `
      <section class="container section">
        <div class="card notice-card">
          <h1>Police CAD access required</h1>
          <p>Regular civilian accounts can use civilian registry, DMV, businesses, applications, complaints, and public rules. Police CAD tools require Owner, Admin, Police, EMS, Dispatch, DOJ, Staff, or approved LEO access.</p>
          <p>Signed in as <strong>${escapeHtml(window.GTAVCAD_CURRENT_USER?.username || window.GTAVCAD_CONTEXT?.username || 'authenticated user')}</strong>.</p>
          <div class="hero-actions">
            <a class="button button-primary" href="/c/${CURRENT_COMMUNITY_SLUG}/">Return to Community Home</a>
            <button type="button" class="button button-secondary" data-auth-logout>Logout</button>
            <a class="button button-secondary" href="dmv.html">DMV</a>
            <a class="button button-secondary" href="businesses.html">Businesses</a>
          </div>
        </div>
      </section>`;
  }
  bindAuthenticatedControls();
  return false;
}

window.canAccessOfficerCad = canAccessOfficerCad;
window.isCadAdminBypass = isCadAdminBypass;
window.enforceCadRoleVisibility = enforceCadRoleVisibility;

async function gtavcadLogout() {
  await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }).catch(() => {});
  ['gtavcad_context', 'GTAVCAD_CONTEXT', 'selected_community', 'selected_community_id', 'impersonating_community', 'impersonating_community_id'].forEach((key) => {
    localStorage.removeItem(key);
    sessionStorage.removeItem(key);
  });
  window.location.href = '/login';
}

async function gtavcadExitImpersonation() {
  await fetch('/api/platform-admin/impersonation/exit', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: '{}' }).catch(() => null);
  window.location.href = '/admin';
}

function bindAuthenticatedControls() {
  document.querySelectorAll('[data-auth-logout]').forEach((button) => {
    if (button.dataset.boundLogout === 'true') return;
    button.dataset.boundLogout = 'true';
    button.addEventListener('click', gtavcadLogout);
  });
  document.querySelectorAll('[data-exit-impersonation]').forEach((button) => {
    if (button.dataset.boundExitImpersonation === 'true') return;
    button.dataset.boundExitImpersonation = 'true';
    button.addEventListener('click', gtavcadExitImpersonation);
  });
}

window.gtavcadLogout = gtavcadLogout;
window.gtavcadExitImpersonation = gtavcadExitImpersonation;


function setCommunityContextState(state = 'empty', details = {}) {
  const map = {
    loading: {community:'Loading…', cad:'Loading…', role:'Loading…', invite:'Loading…'},
    empty: {community:'No active community selected.', cad:'—', role:'Member', invite:'—'},
    logged_out: {community:'Logged out', cad:'Sign in to access community data', role:'Guest', invite:'—'},
    error: {community:'Community unavailable', cad:'Unable to load context', role:'—', invite:'Try again'},
  };
  const payload = Object.assign({}, map[state] || map.empty, details || {});
  document.querySelectorAll('[data-context-community]').forEach((el)=> el.textContent = payload.community);
  document.querySelectorAll('[data-context-cad]').forEach((el)=> el.textContent = payload.cad);
  document.querySelectorAll('[data-context-role]').forEach((el)=> el.textContent = payload.role);
  document.querySelectorAll('[data-context-invite]').forEach((el)=> el.textContent = payload.invite);
}

setCommunityContextState('loading');
setTimeout(() => {
  const stillLoading = [...document.querySelectorAll('[data-context-community],[data-context-cad],[data-context-role],[data-context-invite]')]
    .some(el => /Loading/.test(el.textContent || ''));
  if (stillLoading) setCommunityContextState('error');
}, 7000);

if (CURRENT_COMMUNITY_SLUG && window.fetch) {
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    let url = typeof input === 'string' ? input : input && input.url;
    if (url && url.startsWith('/api/')) {
      const separator = url.includes('?') ? '&' : '?';
      url = `${url}${separator}community_slug=${encodeURIComponent(CURRENT_COMMUNITY_SLUG)}`;
      const mergedInit = { credentials: 'include', ...(init || {}) };
      if (typeof input === 'string') {
        input = url;
      } else {
        input = new Request(url, input);
      }
      return nativeFetch(input, mergedInit);
    }
    return nativeFetch(input, init);
  };
}


function userCanManageCommunity(user = {}) {
  if (user.can_manage_community === true || user.is_community_admin === true) return true;
  const roles = [user.platform_role, user.community_role, user.role].map(normalizeRole);
  return roles.some((role) => COMMUNITY_ADMIN_ACCESS_ROLES.includes(role));
}

function ensureTenantAuthNav(user = {}) {
  if (!CURRENT_COMMUNITY_SLUG) return;
  document.querySelectorAll('.global-nav').forEach((nav) => {
    let admin = nav.querySelector('[data-admin-access-link]');
    if (userCanManageCommunity(user)) {
      if (!admin) {
        admin = document.createElement('a');
        admin.dataset.adminAccessLink = 'true';
        admin.className = 'button button-secondary';
        admin.textContent = 'Admin Access';
        nav.appendChild(admin);
      }
      const qs = CURRENT_COMMUNITY_SLUG ? `?community_slug=${encodeURIComponent(CURRENT_COMMUNITY_SLUG)}` : '';
      admin.href = user.is_platform_owner && !CURRENT_COMMUNITY_SLUG ? '/admin' : `/community-admin${qs}`;
      admin.hidden = false;
    } else if (admin) {
      admin.hidden = true;
    }

    let logout = nav.querySelector('[data-auth-logout]');
    if (!logout) {
      logout = document.createElement('button');
      logout.type = 'button';
      logout.className = 'button button-primary';
      logout.dataset.authLogout = 'true';
      nav.appendChild(logout);
    }
    logout.textContent = user.username ? `Logout (${user.username})` : 'Logout';
  });
  bindAuthenticatedControls();
}

async function applyCommunityBranding() {
  if (!CURRENT_COMMUNITY_SLUG) return null;

  const buildCommunityHref = (target = '') => {
    if (!target || target === '/' || target === 'index' || target === 'index.html') {
      return `/c/${CURRENT_COMMUNITY_SLUG}/`;
    }
    if (target === 'cad' || target === 'cad.html' || target === 'police' || target === 'police.html') {
      return `/c/${CURRENT_COMMUNITY_SLUG}/cad`;
    }
    if (target === 'civilian-portal' || target === 'civilian-dashboard') {
      return `/c/${CURRENT_COMMUNITY_SLUG}/civilian-portal`;
    }
    const normalized = target.endsWith('.html') ? target : `${target}.html`;
    return `/c/${CURRENT_COMMUNITY_SLUG}/${normalized}`;
  };

  const communityLinks = document.querySelectorAll('[data-community-link]');
  communityLinks.forEach((link) => {
    const target = link.getAttribute('data-community-link') || '';
    link.href = buildCommunityHref(target);
  });

  const tenantPageMap = {
    '/': '',
    'rules.html': 'rules.html',
    'civilian.html': 'civilian.html',
    'civilian-portal': 'civilian-portal',
    'civilian-dashboard': 'civilian-dashboard',
    'police.html': 'police.html',
    'cad.html': 'cad.html',
    'dmv.html': 'dmv.html',
    'businesses.html': 'businesses.html',
    'applications.html': 'applications.html',
    'complaints.html': 'complaints.html',
    'donations.html': 'donations.html',
    'join.html': 'join.html',
    'index.html': '',
    'rules': 'rules.html',
    'civilian': 'civilian.html',
    'civilian-portal': 'civilian-portal',
    'civilian-dashboard': 'civilian-dashboard',
    'police': 'police.html',
    'cad': 'cad.html',
    'dmv': 'dmv.html',
    'businesses': 'businesses.html',
    'applications': 'applications.html',
    'complaints': 'complaints.html',
    'donations': 'donations.html',
    'join': 'join.html',
  };
  document.querySelectorAll('a[href]').forEach((link) => {
    const href = link.getAttribute('href');
    if (Object.prototype.hasOwnProperty.call(tenantPageMap, href)) {
      link.href = buildCommunityHref(tenantPageMap[href]);
    }
  });

  removePublicInviteContext();

  try {
    const res = await fetch('/api/communities/context', { credentials: 'include' });
    const data = await res.json();
    if (!res.ok || !data.success) { setCommunityContextState('error'); return null; }
    const community = data.community || {};
    const membership = data.membership || null;
    window.GTAVCAD_CONTEXT = {
      platformName: data.platform?.name || PLATFORM_CONTEXT.name,
      community_id: community.community_id || '',
      community_slug: community.slug || CURRENT_COMMUNITY_SLUG,
      community_name: community.name || '',
      cad_name: community.cad_name || community.name || '',
      communityName: community.name || '',
      communitySlug: community.slug || CURRENT_COMMUNITY_SLUG,
      cadName: community.cad_name || community.name || '',
      username: data.user?.username || '',
      role: membership?.role || data.user?.community_role || data.user?.platform_role || data.user?.role || '',
      platform_role: data.user?.platform_role || '',
      community_role: data.user?.community_role || membership?.role || '',
      department: membership?.department || '',
      can_access_police_cad: data.user?.can_access_police_cad === true,
      is_platform_owner: data.user?.is_platform_owner === true,
      impersonation_active: data.user?.impersonation_active === true,
      colors: {
        primary: community.primary_color || '#ff2d2d',
        secondary: community.secondary_color || '#8b0000',
        accent: community.accent_color || community.primary_color || '#ff2d2d',
        background: community.background_color || '',
        text: community.text_color || '',
      }
    };
    window.GTAVCAD_CURRENT_USER = data.user || window.GTAVCAD_CURRENT_USER || null;
    ensureTenantAuthNav(data.user || {});

    document.title = `${window.GTAVCAD_CONTEXT.cadName || window.GTAVCAD_CONTEXT.communityName} | ${window.GTAVCAD_CONTEXT.platformName}`;
    document.documentElement.style.setProperty('--accent', window.GTAVCAD_CONTEXT.colors.primary);
    document.documentElement.style.setProperty('--accent-dark', window.GTAVCAD_CONTEXT.colors.secondary);
    document.documentElement.style.setProperty('--tenant-accent', window.GTAVCAD_CONTEXT.colors.accent);
    if (window.GTAVCAD_CONTEXT.colors.background) document.documentElement.style.setProperty('--tenant-background', window.GTAVCAD_CONTEXT.colors.background);
    if (window.GTAVCAD_CONTEXT.colors.text) document.documentElement.style.setProperty('--tenant-text', window.GTAVCAD_CONTEXT.colors.text);
    document.querySelectorAll('[data-community-name]').forEach((el) => { el.textContent = window.GTAVCAD_CONTEXT.communityName; });
    document.querySelectorAll('[data-community-cad-name]').forEach((el) => { el.textContent = window.GTAVCAD_CONTEXT.cadName; });
    document.querySelectorAll('.brand').forEach((el) => { el.textContent = window.GTAVCAD_CONTEXT.platformName; });
    const loginTitle = document.querySelector('.officer-login-title');
    if (loginTitle) loginTitle.textContent = window.GTAVCAD_CONTEXT.cadName || `${window.GTAVCAD_CONTEXT.platformName} CAD`;

    const pageHeroTitle = document.querySelector('.page-hero h1, header h1');
    if (pageHeroTitle && document.body.dataset.communityPage === 'true' && pageHeroTitle.dataset.keepTitle !== 'true') {
      if (pageHeroTitle.dataset.tenantTitle) {
        pageHeroTitle.textContent = pageHeroTitle.dataset.tenantTitle.replace('{community}', window.GTAVCAD_CONTEXT.communityName).replace('{cad}', window.GTAVCAD_CONTEXT.cadName);
      }
    }

    document.querySelectorAll('[data-context-community]').forEach((el) => { el.textContent = window.GTAVCAD_CONTEXT.communityName || 'Unknown Community'; });
    const communityCtxCad = document.querySelector('[data-context-cad]');
    if (communityCtxCad) communityCtxCad.textContent = window.GTAVCAD_CONTEXT.cadName || 'CAD';
    const resolvedRole = window.GTAVCAD_CONTEXT.community_role || window.GTAVCAD_CONTEXT.platform_role || window.GTAVCAD_CONTEXT.role || (membership ? membership.role : 'No membership');
    setCommunityContextState('empty', {community: window.GTAVCAD_CONTEXT.communityName || 'No active community selected.', cad: window.GTAVCAD_CONTEXT.cadName || '—', role: resolvedRole || 'Member', invite: membership ? 'Active' : 'Not joined'});
    document.querySelectorAll('[data-context-role]').forEach((el) => { el.textContent = resolvedRole; });
    const resolvedUsername = data.user?.username || 'Unknown';
    document.querySelectorAll('[data-context-username]').forEach((el) => { el.textContent = resolvedUsername; });
    
    const cadTopbarCommunity = document.getElementById('cad-topbar-community');
    if (cadTopbarCommunity) cadTopbarCommunity.textContent = window.GTAVCAD_CONTEXT.communityName || 'Unknown Community';
    const cadTopbarUsername = document.getElementById('cad-topbar-username');
    if (cadTopbarUsername) cadTopbarUsername.textContent = resolvedUsername;
    const cadTopbarRole = document.getElementById('cad-topbar-role');
    if (cadTopbarRole) cadTopbarRole.textContent = resolvedRole;
    const cadTopbarDepartment = document.getElementById('cad-topbar-department');
    if (cadTopbarDepartment) cadTopbarDepartment.textContent = window.GTAVCAD_CONTEXT.department || '—';

    document.querySelectorAll('[data-cad-access-badge]').forEach((el) => { el.textContent = (data.user?.can_access_police_cad === true) ? 'CAD ACCESS' : 'CAD LOCKED'; });
    document.querySelectorAll('[data-exit-impersonation]').forEach((el) => { el.classList.toggle('hidden', data.user?.impersonation_active !== true); });
    bindAuthenticatedControls();
    const communityRoleEl = document.querySelector('[data-context-community-role]');
    if (communityRoleEl) communityRoleEl.textContent = membership?.role || 'Member';
    removePublicInviteContext();
    const cadAccess = Boolean(
      window.GTAVCAD_CURRENT_USER?.can_access_police_cad === true
      || window.GTAVCAD_CONTEXT?.can_access_police_cad === true
    );
    const statusAccess = document.querySelector('[data-status-access]');
    if (statusAccess) statusAccess.textContent = membership ? 'Active Member' : 'No Membership';
    const statusCad = document.querySelector('[data-status-cad]');
    if (statusCad) statusCad.textContent = cadAccess ? 'Authorized' : 'Police CAD access required';
    document.querySelectorAll('[data-cad-link=\"true\"]').forEach((link) => {
      if (cadAccess) return;
      link.href = `/c/${CURRENT_COMMUNITY_SLUG}/cad`;
      link.textContent = 'Locked';
    });
    document.querySelectorAll('[data-context-department]').forEach((el) => { el.textContent = window.GTAVCAD_CONTEXT.department || '—'; });
    enforceCadRoleVisibility();
    window.dispatchEvent(new CustomEvent('gtavcad:context-ready', { detail: window.GTAVCAD_CONTEXT }));

    document.querySelectorAll('[data-tenant-template]').forEach((el) => {
      const template = el.getAttribute('data-tenant-template') || '';
      el.textContent = template
        .replaceAll('{platform}', window.GTAVCAD_CONTEXT.platformName)
        .replaceAll('{community}', window.GTAVCAD_CONTEXT.communityName)
        .replaceAll('{cad}', window.GTAVCAD_CONTEXT.cadName)
        .replaceAll('{role}', window.GTAVCAD_CONTEXT.role || 'No membership')
        .replaceAll('{invite}', '');
    });

    if (!membership && document.body.dataset.communityPage === 'true') {
      const target = document.querySelector('[data-tenant-header]') || document.querySelector('main') || document.body;
      if (!document.getElementById('tenant-membership-error')) {
        const error = document.createElement('div');
        error.id = 'tenant-membership-error';
        error.className = 'card';
        error.style.borderColor = 'var(--accent)';
        error.textContent = 'Membership not found for this community. Please join with an invite or ask an Owner/Admin to activate your access.';
        target.insertAdjacentElement(target.matches('main') ? 'afterbegin' : 'afterend', error);
      }
    }

    return community;
  } catch (error) {
    console.warn('Community branding load failed:', error);
    setCommunityContextState(window.GTAVCAD_CURRENT_USER ? 'error' : 'logged_out');
    return null;
  }
}

const navToggle = document.querySelector('.nav-toggle');
const navMenu = document.querySelector('.global-nav');
const yearSpan = document.querySelectorAll('.current-year');

if (navToggle && navMenu) {
  navToggle.addEventListener('click', () => {
    navMenu.classList.toggle('show');
  });
}

const currentYear = new Date().getFullYear();
if (yearSpan.length) {
  yearSpan.forEach((node) => {
    node.textContent = currentYear;
  });
}

async function refreshAuthNavigation() {
  const navs = document.querySelectorAll('.global-nav');
  if (!navs.length || !window.fetch) return;
  try {
    const res = await fetch('/api/auth/session', { credentials: 'include' });
    if (!res.ok) return;
    const data = await res.json();
    if (!data.success) return;
    window.GTAVCAD_CURRENT_USER = data.user || window.GTAVCAD_CURRENT_USER || null;
    ensureTenantAuthNav(data.user || {});
    document.querySelectorAll('a[href="/login"]').forEach((link) => {
      link.textContent = `Logout (${data.user.username})`;
      link.href = '#logout';
      link.addEventListener('click', async (event) => {
        event.preventDefault();
        await gtavcadLogout();
      }, { once: true });
    });
  } catch (error) {
    console.warn('Auth navigation refresh failed:', error);
  }
}

bindAuthenticatedControls();
refreshAuthNavigation();

// Shared frontend data model. This browser cache is not authoritative for tenant
// context; route slug and server-provided GTAVCAD_CONTEXT always win.
const GTAVCADData = {
  civilians: [],
  vehicles: [],
  licenses: [],
  warrants: [],
  arrests: [],
  incidents: [],
  evidence: [],
  evidenceAttachments: [],
  evidenceAttachmentConfig: { direct_uploads_enabled: false, direct_upload_message: 'Direct uploads are not configured. Attach an external evidence link instead.' },
  trafficStops: [],
  calls911: [],
  casePackets: [],
  gangProfiles: [],
  gangInvestigations: [],
  gangWatchlist: [],
  gangIntelNotes: [],
  gangPackets: [],
  officers: [
    { id: '1L-01', name: 'Chief Unit', status: 'Available', lastUpdate: new Date().toISOString() },
    { id: '2L-12', name: 'Patrol Unit', status: 'En Route', lastUpdate: new Date().toISOString() },
    { id: '3L-22', name: 'Traffic Unit', status: 'On Scene', lastUpdate: new Date().toISOString() },
    { id: 'D-04', name: 'Dispatch', status: 'Active', lastUpdate: new Date().toISOString() },
    { id: 'K9-02', name: 'K9 Unit', status: 'Available', lastUpdate: new Date().toISOString() }
  ],
  activityLog: []
};
// Legacy alias only; not authoritative for tenant context.
const NThaCityData = GTAVCADData;
window.NThaCityData = GTAVCADData;

// Data persistence functions
const CAD_API_URL = '/api/cad';

function saveData() {
  fetch(CAD_API_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(GTAVCADData)
  }).catch((error) => {
    console.warn('CAD save failed:', error);
  });
}

async function loadData() {
  if (loadData._pendingPromise) return loadData._pendingPromise;
  const now = Date.now();
  const minIntervalMs = 750;
  if (!loadData._forceNext && loadData._lastSuccessAt && (now - loadData._lastSuccessAt) < minIntervalMs) {
    return;
  }
  const execute = (async () => {
  try {
    const res = await fetch(CAD_API_URL);
    if (res.ok) {
      const payload = await res.json();
      const data = payload && payload.data ? payload.data : payload;
      Object.assign(GTAVCADData, data);
      try {
        const packetRes = await fetch('/api/cad/case-packets', { credentials: 'include' });
        const packetData = await packetRes.json();
        if (packetRes.ok && packetData.success) GTAVCADData.casePackets = packetData.case_packets || [];
      } catch (packetError) {
        console.warn('Case packet load failed:', packetError);
      }
      try {
        const configRes = await fetch('/api/cad/evidence/attachments/config');
        const configData = await configRes.json();
        if (configRes.ok && configData.success) GTAVCADData.evidenceAttachmentConfig = configData;
        const attachmentRes = await fetch('/api/cad/evidence/attachments');
        const attachmentData = await attachmentRes.json();
        if (attachmentRes.ok && attachmentData.success) GTAVCADData.evidenceAttachments = attachmentData.attachments || [];
      } catch (attachmentError) {
        console.warn('Evidence attachment load failed:', attachmentError);
      }
      window.dispatchEvent(new CustomEvent('gtavcad:data-loaded'));
      loadData._lastSuccessAt = Date.now();
      return;
    }
    console.warn('CAD load failed:', res.status);
    window.dispatchEvent(new CustomEvent('gtavcad:data-error'));
  } catch (error) {
    console.warn('CAD load failed:', error);
    window.dispatchEvent(new CustomEvent('gtavcad:data-error'));
  }
  })();
  loadData._pendingPromise = execute;
  try {
    return await execute;
  } finally {
    loadData._pendingPromise = null;
    loadData._forceNext = false;
  }
}

function requestDataRefresh(options = {}) {
  if (options.force) loadData._forceNext = true;
  if (requestDataRefresh._pendingResolve) {
    requestDataRefresh._pendingResolve();
    requestDataRefresh._pendingResolve = null;
  }
  clearTimeout(requestDataRefresh._timer);
  return new Promise((resolve) => {
    requestDataRefresh._pendingResolve = resolve;
    requestDataRefresh._timer = setTimeout(async () => {
      requestDataRefresh._pendingResolve = null;
      await loadData();
      resolve();
    }, options.delayMs || 125);
  });
}

function generateId(prefix) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

// Add record functions
async function addCivilian(record) {
  const res = await fetch('/api/civilians', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(record),
  });
  const data = await res.json();
  if (!res.ok || !data.success) {
    throw new Error(data.error || 'Civilian save failed');
  }
  if (isOfficerCadPage() && canAccessOfficerCad()) requestDataRefresh();
  return data.civilian;
}

// PHASE 1 REPLACEMENT: Use dedicated /api/dmv/vehicles route instead of legacy saveData
async function addVehicle(record) {
  try {
    const payload = {
      plateNumber: record.plateNumber || record.plate,
      vehicleMake: record.vehicleMake || record.make,
      vehicleModel: record.vehicleModel || record.model,
      vehicleColor: record.vehicleColor || record.color,
      insuranceStatus: record.insuranceStatus || 'Valid',
      registrationStatus: record.registrationStatus || 'Valid',
      ownerName: record.ownerName || '',
      notes: record.notes || '',
      ownerCivilianId: record.ownerCivilianId || '',
    };
    
    const res = await fetch('/api/dmv/vehicles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      throw new Error(data.error || 'Vehicle registration failed');
    }
    // Refresh data from backend after success
    requestDataRefresh();
    return data.vehicle;
  } catch (error) {
    console.error('Vehicle registration error:', error);
    throw error;
  }
}

// PHASE 1 REPLACEMENT: Use dedicated /api/dmv/licenses route instead of legacy saveData
async function addLicense(record) {
  try {
    const payload = {
      licenseName: record.licenseName || record.ownerName,
      licenseClass: record.licenseClass || record.licenseType,
      testStatus: record.testStatus || 'Passed',
      licenseExpiration: record.licenseExpiration || record.expiryDate,
      restrictions: record.restrictions || '',
      status: record.status || 'Valid',
    };
    
    const res = await fetch('/api/dmv/licenses', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      throw new Error(data.error || 'License application failed');
    }
    // Refresh data from backend after success
    requestDataRefresh();
    return data.license;
  } catch (error) {
    console.error('License application error:', error);
    throw error;
  }
}

// PHASE 1 REPLACEMENT: Use dedicated /api/dispatch/calls route instead of legacy saveData
async function add911Call(record) {
  try {
    const payload = {
      caller_name: record.callerName || record.caller || '',
      location: record.location || '',
      call_type: record.incidentType || record.callType || '',
      description: record.description || '',
      priority: record.priority || 'Medium',
    };
    
    if (!payload.caller_name || !payload.location || !payload.call_type) {
      throw new Error('Missing required fields: caller_name, location, call_type');
    }
    
    const res = await fetch('/api/dispatch/calls', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      throw new Error(data.error || '911 call creation failed');
    }
    // Refresh data from backend after success
    requestDataRefresh();
    return data;
  } catch (error) {
    console.error('911 call error:', error);
    throw error;
  }
}

async function addTrafficStop(record) {
  const payload = { ...record, id: record.id || record.traffic_stop_id || generateId('stop') };
  const res = await fetch('/api/cad/traffic-stops', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok || !data.success) throw new Error(data.error || 'Traffic stop save failed');
  requestDataRefresh();
  return data.traffic_stop || { ...payload, id: data.traffic_stop_id, createdAt: new Date().toISOString() };
}

async function addArrest(record) {
  const payload = { ...record, id: record.id || generateId('arr') };
  const res = await fetch('/api/cad/arrests', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok || !data.success) {
    throw new Error(data.error || 'Arrest report save failed');
  }
  requestDataRefresh();
  return data.arrest;
}

async function addEvidence(record, formElement = null) {
  if (formElement) {
    const payload = new FormData(formElement);
    if (!payload.get('case_id') && payload.get('caseNumber')) payload.set('case_id', payload.get('caseNumber'));
    if (!payload.get('description') && payload.get('evidenceDescription')) payload.set('description', payload.get('evidenceDescription'));
    if (!payload.get('category') && payload.get('evidenceType')) payload.set('category', payload.get('evidenceType'));
    if (!payload.get('external_url') && payload.get('evidenceLink')) payload.set('external_url', payload.get('evidenceLink'));
    if (!payload.get('case_id') && !payload.get('evidence_id') && !payload.get('arrest_id') && !payload.get('warrant_id') && !payload.get('court_packet_id')) {
      console.info('No case selected. GTAVCAD will create a new case record for this evidence.');
    }
    const res = await fetch('/api/cad/evidence/attachments', { method: 'POST', credentials: 'include', body: payload });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || 'Evidence attachment save failed');
    if (data.case_number) {
      data.attachment.generated_case_number = data.case_number;
      window.dispatchEvent(new CustomEvent('gtavcad:evidence-case-generated', { detail: { case_number: data.case_number, evidence_id: data.evidence_id } }));
    }
    requestDataRefresh();
    return data.attachment;
  }
  record.id = generateId('evd');
  record.createdAt = new Date().toISOString();
  GTAVCADData.evidence.push(record);
  saveData();
  return record;
}

async function addWarrant(record) {
  const res = await fetch('/api/cad/warrants', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify(record),
  });
  const data = await res.json();
  if (!res.ok || !data.success) {
    const details = data.details && Array.isArray(data.details.errors) ? `: ${data.details.errors.join(', ')}` : '';
    throw new Error(`${data.error || 'Warrant save failed'}${details}`);
  }
  requestDataRefresh();
  return data.warrant;
}

function addIncident(record) {
  record.id = generateId('inc');
  record.createdAt = new Date().toISOString();
  GTAVCADData.incidents.push(record);
  saveData();
  return record;
}

function addActivity(type, message) {
  const activity = {
    id: generateId('act'),
    type: type,
    message: message,
    timestamp: new Date().toISOString()
  };
  GTAVCADData.activityLog.unshift(activity);
  // Keep only the last 50 activities
  if (GTAVCADData.activityLog.length > 50) {
    GTAVCADData.activityLog = GTAVCADData.activityLog.slice(0, 50);
  }
  saveData();
  renderActivityFeed();
}

// Lookup functions
async function lookupCivilian(query) {
  if (!query || query.trim() === '') return [];

  const params = new URLSearchParams({ q: query.trim() });
  const res = await fetch(`/api/civilians?${params.toString()}`);
  const data = await res.json();
  if (!res.ok || !data.success) {
    throw new Error(data.error || 'Civilian lookup failed');
  }
  return data.civilians || [];
}

function lookupVehiclePlate(plate) {
  if (!plate || plate.trim() === '') return [];

  const normalizedPlate = normalizePlate(plate);
  return GTAVCADData.vehicles.filter(veh =>
    normalizePlate(veh.plate) === normalizedPlate ||
    (veh.ownerName && veh.ownerName.toLowerCase().includes(plate.toLowerCase())) ||
    (veh.vehicleMake && veh.vehicleMake.toLowerCase().includes(plate.toLowerCase())) ||
    (veh.vehicleModel && veh.vehicleModel.toLowerCase().includes(plate.toLowerCase()))
  );
}

// Helper functions
function getFormData(form) {
  const data = {};
  const formData = new FormData(form);
  for (let [key, value] of formData.entries()) {
    data[key] = value;
  }
  return data;
}

function normalizePlate(plate) {
  return plate.toUpperCase().replace(/[^A-Z0-9]/g, '');
}

function formatDate(dateString) {
  return new Date(dateString).toLocaleDateString();
}

function showFormMessage(form, message, type = 'success') {
  const status = form.querySelector('.form-status');
  if (status) {
    status.textContent = message;
    status.className = `form-status ${type}`;
  }
}

// Render functions
function renderCivilianPreview(record) {
  const container = document.getElementById('civilian-preview');
  if (!container) return;

  container.innerHTML = `
    <div class="record-preview">
      <h3>Civilian Record Created</h3>
      <div class="record-grid">
        <div><strong>Name:</strong> ${record.firstName} ${record.lastName}</div>
        <div><strong>Civilian ID:</strong> ${record.id}</div>
        <div><strong>DOB:</strong> ${record.dob}</div>
        <div><strong>Phone:</strong> ${record.phone}</div>
        <div><strong>Discord:</strong> ${record.discord}</div>
        <div><strong>Address:</strong> ${record.address}</div>
        <div><strong>Occupation:</strong> ${record.occupation}</div>
        <div><strong>Driver License:</strong> ${record.driverLicense}</div>
        <div><strong>Vehicle:</strong> ${record.vehicleMake} ${record.vehicleModel} (${record.plate})</div>
        <div><strong>Created:</strong> ${formatDate(record.createdAt)}</div>
      </div>
    </div>
  `;
}

function renderLookupResults(container, results, type) {
  if (!container) return;

  if (results.length === 0) {
    container.innerHTML = `<div class="result-card"><div class="empty-state"><div class="empty-icon">🔍</div><h3>No ${type} records found</h3><p>No local records match your search criteria.</p></div></div>`;
    return;
  }

  const html = results.map(result => {
    if (type === 'civilian') {
      return `
        <div class="result-card">
          <div class="result-header">
            <div class="result-title">${result.firstName} ${result.lastName}</div>
            <div class="result-badge badge badge-primary">Civilian ID: ${result.id}</div>
          </div>
          <div class="result-grid">
            <div class="result-field">
              <div class="result-label">Full Name</div>
              <div class="result-value">${result.firstName} ${result.lastName}</div>
            </div>
            <div class="result-field">
              <div class="result-label">Date of Birth</div>
              <div class="result-value">${result.dob}</div>
            </div>
            <div class="result-field">
              <div class="result-label">Discord</div>
              <div class="result-value">${result.discord}</div>
            </div>
            <div class="result-field">
              <div class="result-label">Phone</div>
              <div class="result-value">${result.phone}</div>
            </div>
            <div class="result-field">
              <div class="result-label">Address</div>
              <div class="result-value">${result.address}</div>
            </div>
            <div class="result-field">
              <div class="result-label">Occupation</div>
              <div class="result-value">${result.occupation}</div>
            </div>
            <div class="result-field">
              <div class="result-label">Driver License</div>
              <div class="result-value">${result.driverLicense || 'None'}</div>
            </div>
            <div class="result-field">
              <div class="result-label">Firearm License</div>
              <div class="result-value">${result.firearmLicense || 'None'}</div>
            </div>
            <div class="result-field">
              <div class="result-label">Business License</div>
              <div class="result-value">${result.businessLicense || 'None'}</div>
            </div>
            <div class="result-field">
              <div class="result-label">Vehicle</div>
              <div class="result-value">${result.vehicleMake} ${result.vehicleModel} (${result.plate})</div>
            </div>
            <div class="result-field">
              <div class="result-label">Insurance</div>
              <div class="result-value">${result.insuranceStatus || 'Unknown'}</div>
            </div>
            <div class="result-notes">
              <div class="result-label">Criminal Background</div>
              <div class="result-value">${result.hasCriminalHistory ? 'Criminal record present. See related records below.' : (result.criminalNotes || 'No criminal history on file')}</div>
            </div>
          </div>
        </div>
      `;
    } else if (type === 'vehicle') {
      return `
        <div class="result-card">
          <div class="result-header">
            <div class="result-title">Plate: ${result.plate}</div>
            <div class="result-badge badge badge-primary">Vehicle ID: ${result.id}</div>
          </div>
          <div class="result-grid">
            <div class="result-field">
              <div class="result-label">Make/Model/Year</div>
              <div class="result-value">${result.vehicleMake} ${result.vehicleModel} (${result.vehicleYear})</div>
            </div>
            <div class="result-field">
              <div class="result-label">Color</div>
              <div class="result-value">${result.vehicleColor}</div>
            </div>
            <div class="result-field">
              <div class="result-label">Registered Owner</div>
              <div class="result-value">${result.ownerName}</div>
            </div>
            <div class="result-field">
              <div class="result-label">Civilian ID</div>
              <div class="result-value">${result.civilianId || 'Unknown'}</div>
            </div>
            <div class="result-field">
              <div class="result-label">Insurance Status</div>
              <div class="result-value">${result.insuranceStatus}</div>
            </div>
            <div class="result-field">
              <div class="result-label">Registration Status</div>
              <div class="result-value">${result.registrationStatus}</div>
            </div>
            <div class="result-notes">
              <div class="result-label">Notes/Flags</div>
              <div class="result-value">${result.notes || 'No additional notes'}</div>
            </div>
          </div>
        </div>
      `;
    }
    return '';
  }).join('');

  container.innerHTML = html;
}

// Render call queue
function renderCallQueue() {
  const container = document.getElementById('call-queue');
  if (!container) return;

  const activeCalls = GTAVCADData.calls911.filter(c => c.status !== 'Closed');

  if (activeCalls.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">📞</div>
        <h3>No active 911 calls.</h3>
        <p>All emergency calls have been resolved.</p>
      </div>
    `;
    return;
  }

  const html = activeCalls.map(call => {
    const priorityClass = call.priority ? `priority-${call.priority.toLowerCase()}` : 'priority-low';
    const statusClass = call.status ? `status-${call.status.toLowerCase().replace(' ', '-')}` : 'status-new';

    return `
      <div class="call-card">
        <div class="call-header">
          <span class="call-id">${call.id}</span>
          <span class="badge ${priorityClass}">${call.priority || 'Low'}</span>
        </div>
        <div class="call-details">
          <div><strong>Caller:</strong> ${call.callerName}</div>
          <div><strong>Location:</strong> ${call.location}</div>
          <div><strong>Type:</strong> ${call.incidentType}</div>
          <div><strong>Assigned:</strong> ${call.assignedUnit || 'Unassigned'}</div>
          <div><strong>Status:</strong> <span class="badge ${statusClass}">${call.status || 'New'}</span></div>
          <div><strong>Created:</strong> ${formatDate(call.createdAt)}</div>
        </div>
        <div class="call-description">
          ${call.description}
        </div>
        <div class="call-actions">
          <button class="button button-secondary" onclick="updateCallStatus('${call.id}', 'Assigned')">Mark Assigned</button>
          <button class="button button-secondary" onclick="updateCallStatus('${call.id}', 'En Route')">Mark En Route</button>
          <button class="button button-secondary" onclick="updateCallStatus('${call.id}', 'On Scene')">Mark On Scene</button>
          <button class="button button-primary" onclick="updateCallStatus('${call.id}', 'Closed')">Close Call</button>
        </div>
      </div>
    `;
  }).join('');

  container.innerHTML = html;
}

// Render activity feed
function renderActivityFeed() {
  const container = document.getElementById('activity-feed');
  if (!container) return;

  const activities = GTAVCADData.activityLog.slice(0, 10);

  if (activities.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">📋</div>
        <h3>No recent activity</h3>
        <p>System activity will appear here.</p>
      </div>
    `;
    return;
  }

  const html = activities.map(activity => `
    <div class="activity-item">
      <div class="activity-header">
        <span class="activity-type">${activity.type}</span>
        <span class="activity-time">${formatTime(activity.timestamp)}</span>
      </div>
      <div class="activity-message">${activity.message}</div>
    </div>
  `).join('');

  container.innerHTML = html;
}

// Render warrants table
function renderWarrantsTable(filter = 'active') {
  const tbody = document.getElementById('warrants-tbody');
  if (!tbody) return;

  let warrants = GTAVCADData.warrants || [];

  switch (filter) {
    case 'active': warrants = warrants.filter(w => (w.status || w.warrantStatus) === 'Active'); break;
    case 'served': warrants = warrants.filter(w => (w.status || w.warrantStatus) === 'Served'); break;
    case 'expired': warrants = warrants.filter(w => (w.status || w.warrantStatus) === 'Expired'); break;
    case 'withdrawn': warrants = warrants.filter(w => (w.status || w.warrantStatus) === 'Withdrawn'); break;
    case 'all': break;
  }

  if (warrants.length === 0) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty-row">No ${escapeHtml(filter)} warrants found.</td></tr>`;
    return;
  }

  const html = warrants.map(warrant => {
    const id = warrant.warrant_id || warrant.id;
    const number = warrant.warrant_number || id;
    const type = warrant.warrant_type || 'Arrest Warrant';
    const subject = warrant.subject_name || warrant.suspectName || warrant.warrantName || '—';
    const basis = warrant.charges_or_basis || warrant.charges || warrant.warrantCharges || '—';
    const agency = warrant.issuing_agency || warrant.issuer || warrant.warrantIssuer || '—';
    const authority = warrant.judge_or_authority || '—';
    const expiration = warrant.expiration_date || warrant.expiration || warrant.expirationDate || '—';
    const status = warrant.status || warrant.warrantStatus || 'Active';
    const downloadUrl = warrant.pdf_download_url || '';
    const pdfStatus = warrant.pdf_generated_at ? '<span class="badge badge-success">Generated</span>' : '<span class="badge badge-secondary">Not generated</span>';
    const downloadButton = downloadUrl ? `<a class="button button-secondary" href="${escapeAttr(downloadUrl)}">Download PDF</a>` : '';
    return `
      <tr>
        <td>${escapeHtml(number)}</td>
        <td>${escapeHtml(type)}</td>
        <td>${escapeHtml(subject)}</td>
        <td>${escapeHtml(basis)}</td>
        <td>${escapeHtml(agency)}</td>
        <td>${escapeHtml(authority)}</td>
        <td>${escapeHtml(expiration)}</td>
        <td><span class="badge badge-${status === 'Active' ? 'warning' : 'secondary'}">${escapeHtml(status)}</span></td>
        <td>${pdfStatus}</td>
        <td class="table-actions">
          <button class="button button-ghost" onclick="viewWarrant('${escapeAttr(id)}')">View</button>
          ${status === 'Active' ? `
            <button class="button button-success" onclick="updateWarrantStatus('${escapeAttr(id)}', 'Served')">Served</button>
            <button class="button button-warning" onclick="updateWarrantStatus('${escapeAttr(id)}', 'Expired')">Expired</button>
            <button class="button button-secondary" onclick="updateWarrantStatus('${escapeAttr(id)}', 'Withdrawn')">Withdraw</button>
          ` : ''}
          <button class="button button-primary" onclick="generateWarrantPdf('${escapeAttr(id)}')">Generate PDF</button>
          <button class="button button-secondary" onclick="generateCasePacket({warrant_id:'${escapeAttr(id)}'})">Generate Case Packet</button>
          ${downloadButton}
        </td>
      </tr>
    `;
  }).join('');

  tbody.innerHTML = html;
}

// Render arrests table
function renderArrestsTable() {
  const tbody = document.getElementById('arrests-tbody');
  if (!tbody) return;

  const arrests = GTAVCADData.arrests.sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));

  if (arrests.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty-row">No arrest records have been filed locally yet.</td></tr>`;
    return;
  }

  const html = arrests.map(arrest => `
    <tr>
      <td>${arrest.id}</td>
      <td>${arrest.suspectName}</td>
      <td>${arrest.charges}</td>
      <td>${arrest.arrestingOfficer}</td>
      <td>${arrest.location}</td>
      <td>${arrest.penalty}</td>
      <td>${arrest.evidenceAttached}</td>
      <td>${formatDate(arrest.createdAt)}<br><button class="button button-secondary" onclick="generateCasePacket({arrest_id:'${escapeAttr(arrest.id)}',civilian_id:'${escapeAttr(arrest.civilianId || arrest.civilian_id || '')}'})">Generate Case Packet</button></td>
    </tr>
  `).join('');

  tbody.innerHTML = html;
}

// Render traffic stops table
function renderTrafficTable() {
  const tbody = document.getElementById('traffic-tbody');
  if (!tbody) return;

  const stops = GTAVCADData.trafficStops.sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));

  if (stops.length === 0) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty-row">No traffic stops logged yet.</td></tr>`;
    return;
  }

  const html = stops.map(stop => {
    let outcomeClass = 'badge-secondary';
    if (stop.outcome) {
      switch (stop.outcome.toLowerCase()) {
        case 'warning': outcomeClass = 'badge-warning'; break;
        case 'citation': outcomeClass = 'badge-warning'; break;
        case 'arrest': outcomeClass = 'badge-alert'; break;
        case 'vehicle impounded': outcomeClass = 'badge-warning'; break;
        case 'released': outcomeClass = 'badge-success'; break;
        default: outcomeClass = 'badge-secondary';
      }
    }
    return `
      <tr>
        <td>${stop.id}</td>
        <td>${stop.officerName}</td>
        <td>${stop.driverName}</td>
        <td>${stop.plate}</td>
        <td>${stop.vehicleInfo}</td>
        <td>${stop.location}</td>
        <td>${stop.reason}</td>
        <td><span class="badge ${outcomeClass}">${stop.outcome || 'Unknown'}</span></td>
        <td>${stop.notes || 'None'}</td>
        <td>${formatDate(stop.createdAt)}</td>
      </tr>
    `;
  }).join('');

  tbody.innerHTML = html;
}

// Render evidence table
function renderEvidenceTable() {
  const tbody = document.getElementById('evidence-tbody');
  if (!tbody) return;

  const legacyEvidence = (GTAVCADData.evidence || []).map(item => ({ ...item, recordKind: 'legacy' }));
  const attachments = (GTAVCADData.evidenceAttachments || []).map(item => ({ ...item, recordKind: 'attachment' }));
  const evidence = [...attachments, ...legacyEvidence].sort((a, b) => new Date(b.created_at || b.createdAt || 0) - new Date(a.created_at || a.createdAt || 0));

  if (evidence.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty-row">No evidence submitted yet.</td></tr>`;
    return;
  }

  const safeEvidenceUrl = (value) => {
    try {
      const parsed = new URL(String(value || ''), window.location.origin);
      return ['http:', 'https:'].includes(parsed.protocol) ? parsed.href : '';
    } catch (error) {
      return '';
    }
  };

  const html = evidence.map(item => {
    if (item.recordKind === 'attachment') {
      const safeDownload = safeEvidenceHref(item.download_url, { allowInternalDownload: true });
      const openAction = safeDownload
        ? `<a class="button button-secondary" href="${escapeAttr(safeDownload)}">Download</a>`
        : (item.external_url ? evidenceLinkAction(item.external_url, 'Open Link', 'button button-secondary') : 'None');
      const action = `${openAction} <button class="button button-ghost" type="button" onclick="deleteEvidenceAttachment('${escapeAttr(item.attachment_id)}')">Delete</button>`;
      const size = item.file_size ? `${Math.round(item.file_size / 1024)} KB` : '—';
      return `
        <tr data-evidence-id="${escapeAttr(item.evidence_id || '')}" data-attachment-id="${escapeAttr(item.attachment_id || '')}" data-evidence-attachment-id="${escapeAttr(item.attachment_id || '')}">
          <td>${escapeHtml(item.attachment_id)}</td>
          <td>${escapeHtml(item.case_id || item.evidence_id || item.arrest_id || item.warrant_id || item.court_packet_id || '—')}</td>
          <td>${escapeHtml(item.uploaded_by?.username || item.uploaded_by?.user_id || '—')}</td>
          <td><span class="badge badge-secondary">${escapeHtml(item.file_type || 'link')}</span></td>
          <td>${escapeHtml(item.description || item.original_filename || '—')}<br><small>${escapeHtml(item.category || '')} ${escapeHtml(size)}</small></td>
          <td>${action}</td>
          <td><span class="badge badge-submitted">${escapeHtml(item.review_status || 'submitted')}</span></td>
          <td>${formatDate(item.created_at)}</td>
        </tr>
      `;
    }
    const isCasePacket = String(item.type || item.evidenceType || '').toUpperCase() === 'CASE PACKET';
    const storageClass = item.storageStatus ? `badge-${String(item.storageStatus).toLowerCase().replace(' ', '-')}` : 'badge-secondary';
    const hasFile = Boolean(item.link || item.evidenceLink);
    return `
      <tr data-evidence-id="${escapeAttr(item.id || '')}">
        <td>${escapeHtml(item.id)}</td>
        <td>${escapeHtml(item.caseNumber)}</td>
        <td>${escapeHtml(item.officer || item.evidenceOfficer)}</td>
        <td>${escapeHtml(isCasePacket ? 'CASE PACKET PDF' : (item.type || item.evidenceType))}</td>
        <td>${escapeHtml(isCasePacket ? `Generated court packet for ${item.subject_name || 'Unknown Subject'} — ${item.charges || item.caseNumber || 'Case'}` : (item.description || item.evidenceDescription))}</td>
        <td>${hasFile ? evidenceLinkAction(item.link || item.evidenceLink, 'Download') : 'No file attached'}</td>
        <td><span class="badge ${escapeAttr(storageClass)}">${escapeHtml(item.storageStatus || (hasFile ? 'GENERATED' : 'METADATA ONLY'))}</span></td>
        <td>${formatDate(item.createdAt)}</td>
      </tr>
    `;
  }).join('');

  tbody.innerHTML = html;
}


async function deleteEvidenceAttachment(attachmentId) {
  if (!attachmentId) return;
  if (!window.confirm('Soft delete this evidence attachment?')) return;
  try {
    const res = await fetch(`/api/cad/evidence/attachments/${encodeURIComponent(attachmentId)}`, { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || 'Evidence attachment delete failed');
    requestDataRefresh();
    renderEvidenceTable();
    showToast('Evidence attachment deleted', 'success');
  } catch (err) {
    showToast(err.message || 'Evidence attachment delete failed', 'error');
  }
}
window.deleteEvidenceAttachment = deleteEvidenceAttachment;

// Render officers board
function renderOfficersBoard() {
  const container = document.getElementById('officers-board');
  if (!container) return;

  const html = GTAVCADData.officers.map(officer => {
    const officerId = escapeHtml(officer.id);
    const officerStatus = String(officer.status || '');
    return `
    <div class="officer-card">
      <div class="officer-header">
        <span class="officer-callsign">${officerId}</span>
        <select class="officer-status-select" onchange="updateOfficerStatus('${officerId}', this.value)">
          <option value="Available" ${officerStatus === 'Available' ? 'selected' : ''}>Available</option>
          <option value="Assigned" ${officerStatus === 'Assigned' ? 'selected' : ''}>Assigned</option>
          <option value="En Route" ${officerStatus === 'En Route' ? 'selected' : ''}>En Route</option>
          <option value="On Scene" ${officerStatus === 'On Scene' ? 'selected' : ''}>On Scene</option>
          <option value="Busy" ${officerStatus === 'Busy' ? 'selected' : ''}>Busy</option>
          <option value="On Duty" ${officerStatus === 'On Duty' ? 'selected' : ''}>On Duty</option>
          <option value="Off Duty" ${officerStatus === 'Off Duty' ? 'selected' : ''}>Off Duty</option>
        </select>
      </div>
      <div class="officer-role">${escapeHtml(officer.name)}</div>
      <div class="officer-last-update">Updated: ${escapeHtml(formatTime(officer.lastUpdate))}</div>
    </div>
  `;
  }).join('');

  container.innerHTML = html;
}

// Helper functions
function formatTime(dateString) {
  const date = new Date(dateString);
  const now = new Date();
  const diff = now - date;
  const minutes = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);

  if (minutes < 1) return 'Just now';
  if (minutes < 60) return `${minutes}m ago`;
  if (hours < 24) return `${hours}h ago`;
  return `${days}d ago`;
}

// Update call status
function updateCallStatus(callId, newStatus) {
  const call = GTAVCADData.calls911.find(c => c.id === callId);
  if (call) {
    call.status = newStatus;
    saveData();
    updateDashboard();
    renderCallQueue();
    addActivity('Call Update', `Call ${callId} status changed to ${newStatus}`);
    showToast(`Call ${callId} marked as ${newStatus}`, 'success');
  }
}

// Update warrant status
async function updateWarrantStatus(warrantId, newStatus) {
  try {
    const res = await fetch(`/api/cad/warrants/${encodeURIComponent(warrantId)}/status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ status: newStatus }),
    });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || 'Warrant status update failed');
    await requestDataRefresh();
    updateDashboard();
    renderWarrantsTable();
    addActivity('Warrant Update', `Warrant ${warrantId} marked as ${newStatus}`);
    showToast(`Warrant ${warrantId} marked as ${newStatus}`, 'success');
  } catch (err) {
    showToast(err.message || 'Warrant status update failed', 'error');
  }
}

function viewWarrant(warrantId) {
  const warrant = (GTAVCADData.warrants || []).find(w => (w.warrant_id || w.id) === warrantId);
  if (!warrant) return showToast('Warrant not found', 'error');
  const details = [
    `Warrant: ${warrant.warrant_number || warrantId}`,
    `Type: ${warrant.warrant_type || 'Arrest Warrant'}`,
    `Subject: ${warrant.subject_name || warrant.suspectName || warrant.warrantName || '—'}`,
    `Basis: ${warrant.charges_or_basis || warrant.charges || warrant.warrantCharges || '—'}`,
    `Probable Cause: ${warrant.probable_cause || warrant.notes || warrant.warrantNotes || '—'}`
  ].join('\n');
  alert(details);
}

async function generateWarrantPdf(warrantId) {
  try {
    const res = await fetch(`/api/cad/warrants/${encodeURIComponent(warrantId)}/generate-pdf`, { method: 'POST', credentials: 'include' });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || 'Warrant PDF generation failed');
    await requestDataRefresh();
    renderWarrantsTable();
    if (typeof renderEvidenceTable === 'function') renderEvidenceTable();
    showToast('Warrant PDF generated. Added to evidence log.', 'success');
  } catch (err) {
    showToast(err.message || 'Warrant PDF generation failed', 'error');
  }
}

window.updateWarrantStatus = updateWarrantStatus;
window.viewWarrant = viewWarrant;
window.generateWarrantPdf = generateWarrantPdf;

// Update officer status
async function updateOfficerStatus(officerId, newStatus) {
  const officer = GTAVCADData.officers.find(o => o.id === officerId);
  if (officer) {
    officer.status = newStatus;
    officer.lastUpdate = new Date().toISOString();
    try {
      await fetch('/api/officer-status', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: officerId,
          status: newStatus,
          name: officer.name || officerId,
          department: officer.department || '',
        }),
      });
    } catch (error) {
      console.warn('Officer status update failed:', error);
    }
    saveData();
    updateDashboard();
    renderOfficersBoard();
    addActivity('Officer Status', `${officerId} status changed to ${newStatus}`);
    showToast(`${officerId} status updated to ${newStatus}`, 'info');
  }
}

// Toast notification system
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `
    <div class="toast-icon">${getToastIcon(type)}</div>
    <div class="toast-content">
      <div class="toast-title">${getToastTitle(type)}</div>
      <div class="toast-message">${message}</div>
    </div>
    <button class="toast-close" onclick="this.parentElement.remove()">×</button>
  `;

  container.appendChild(toast);

  // Auto remove after 5 seconds
  setTimeout(() => {
    if (toast.parentElement) {
      toast.remove();
    }
  }, 5000);
}

function getToastIcon(type) {
  switch (type) {
    case 'success': return '✓';
    case 'warning': return '⚠';
    case 'error': return '✕';
    case 'info': return 'ℹ';
    default: return 'ℹ';
  }
}

function getToastTitle(type) {
  switch (type) {
    case 'success': return 'Success';
    case 'warning': return 'Warning';
    case 'error': return 'Error';
    case 'info': return 'Info';
    default: return 'Info';
  }
}

// Dashboard update function
function updateDashboard() {
  // Active Units - count officers that are not Off Duty
  const activeUnitsEl = document.getElementById('active-units');
  if (activeUnitsEl) {
    const activeUnits = GTAVCADData.officers.filter(o => o.status !== 'Off Duty').length;
    activeUnitsEl.textContent = activeUnits;
  }

  // Pending Calls - calls not closed
  const pendingCallsEl = document.getElementById('pending-calls');
  if (pendingCallsEl) {
    const pendingCalls = GTAVCADData.calls911.filter(c => c.status !== 'Closed').length;
    pendingCallsEl.textContent = pendingCalls;
  }

  // Critical Calls - calls with priority Critical
  const criticalCallsEl = document.getElementById('critical-calls');
  if (criticalCallsEl) {
    const criticalCalls = GTAVCADData.calls911.filter(c => c.priority === 'Critical' && c.status !== 'Closed').length;
    criticalCallsEl.textContent = criticalCalls;
  }

  // Active Warrants - warrants with status Active
  const activeWarrantsEl = document.getElementById('active-warrants');
  if (activeWarrantsEl) {
    const activeWarrants = GTAVCADData.warrants.filter(w => w.status === 'Active').length;
    activeWarrantsEl.textContent = activeWarrants;
  }

  // Recent Arrests - total arrests
  const recentArrestsEl = document.getElementById('recent-arrests');
  if (recentArrestsEl) {
    recentArrestsEl.textContent = GTAVCADData.arrests.length;
  }

  // Open Reports - total incidents
  const openReportsEl = document.getElementById('open-reports');
  if (openReportsEl) {
    openReportsEl.textContent = GTAVCADData.incidents.length;
  }

  // Evidence Items - total evidence
  const evidenceItemsEl = document.getElementById('evidence-items');
  if (evidenceItemsEl) {
    evidenceItemsEl.textContent = GTAVCADData.evidence.length;
  }

  // Traffic Stops - total traffic stops
  const trafficStopsEl = document.getElementById('traffic-stops');
  if (trafficStopsEl) {
    trafficStopsEl.textContent = GTAVCADData.trafficStops.length;
  }
}

// Form handlers
function handleCivilianForm() {
  const form = document.getElementById('civilian-form');
  if (!form) return;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const raw = getFormData(form);

    // Send the Civilian Registration form payload to the API; PostgreSQL is the source of truth.
    const payload = { ...raw };

    const submitBtn = form.querySelector('[type="submit"]');
    if (submitBtn) submitBtn.disabled = true;
    showFormMessage(form, 'Saving civilian profile…');

    try {
      const res = await fetch('/api/civilians', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json();

      if (res.ok && data.success) {
        const record = data.civilian || { id: data.civilian_id, ...raw };
        renderCivilianPreview({ ...record, discord: raw.discord || '' });
        const dashboardUrl = data.dashboard_url || getCivilianDashboardUrl(data.civilian_id);
        showFormMessage(form, `✅ Civilian profile created. Opening Civilian Dashboard… <a class="button button-secondary" href="${escapeAttr(dashboardUrl)}">Open Civilian Dashboard</a>`);
        showToast(`Civilian profile created.`, 'success');
        form.reset();
        window.location.href = dashboardUrl;
      } else {
        showFormMessage(form, `❌ Error: ${data.error || 'Registration failed'}`, 'error');
      }
    } catch (err) {
      showFormMessage(form, `❌ Network error: ${err.message}`, 'error');
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });
}

function handle911Form() {
  const form = document.getElementById('dispatch-form');
  if (!form) return;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const statusEl = document.getElementById('dispatch-status');
    const submitButton = form.querySelector('button[type="submit"]');
    
    try {
      submitButton.disabled = true;
      if (statusEl) {
        statusEl.textContent = 'Creating dispatch call...';
        statusEl.style.color = 'var(--muted)';
        statusEl.style.display = 'block';
      }
      
      const data = getFormData(form);
      await add911Call(data);
      
      updateDashboard();
      renderCallQueue();
      addActivity('911 Call', `New call created: ${data.incidentType} at ${data.location}`);
      showToast('911 call logged successfully', 'success');
      
      if (statusEl) {
        statusEl.textContent = 'Call sent to dispatch successfully!';
        statusEl.style.color = '#4caf50';
      }
      document.dispatchEvent(new CustomEvent('gtavcad:911-submit-success'));
      form.reset();
    } catch (error) {
      if (statusEl) {
        statusEl.textContent = 'Unable to submit call right now. Please try again.';
        statusEl.style.color = '#ff6b6b';
        statusEl.style.display = 'block';
      }
      showToast('911 call failed. Please try again.', 'error');
      document.dispatchEvent(new CustomEvent('gtavcad:911-submit-error'));
    } finally {
      submitButton.disabled = false;
    }
  });
}


async function generateCasePacket(payload = {}) {
  try {
    const res = await fetch('/api/cad/case-packets/generate', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || 'Case packet generation failed');
    requestDataRefresh();
    renderCasePacketsTable();
    renderEvidenceTable();
    showToast(`Case packet generated: ${data.case_id}`, 'success');
    return data.case_packet;
  } catch (err) {
    showToast(err.message || 'Case packet generation failed', 'error');
    throw err;
  }
}

function renderCasePacketsTable() {
  const tbody = document.getElementById('case-packets-tbody');
  if (!tbody) return;
  const packets = (GTAVCADData.casePackets || []).sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
  if (!packets.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="empty-row">No case packets generated yet.</td></tr>';
    return;
  }
  tbody.innerHTML = packets.map((p) => {
    const status = (p.status || 'open');
    const viewButton = `<button class="button button-ghost" onclick="viewCasePacket('${escapeAttr(p.case_id)}')">View</button>`;
    const downloadButton = p.download_url ? `<a class="button button-secondary" href="${escapeAttr(p.download_url)}">Download PDF</a>` : '';
    const linkedEvidenceIdsRaw = p.linked_evidence_ids || p.linked_evidence_id || p.evidence_id || p.evidence_attachment_id || [];
    const linkedEvidenceIds = Array.isArray(linkedEvidenceIdsRaw)
      ? linkedEvidenceIdsRaw.filter(Boolean).map((value) => String(value))
      : String(linkedEvidenceIdsRaw || '').split(',').map((value) => value.trim()).filter(Boolean);
    const linkedEvidencePrimary = linkedEvidenceIds[0] || '';
    const linkedEvidenceCount = linkedEvidenceIds.length;
    const evidenceButton = linkedEvidencePrimary
      ? `<button class="button button-secondary" onclick="focusEvidenceItem('${escapeAttr(linkedEvidencePrimary)}')">Open Evidence${linkedEvidenceCount > 1 ? ` (${linkedEvidenceCount})` : ''}</button>`
      : '';
    const deleteButton = `<button class="button button-ghost" onclick="deleteCasePacket('${escapeAttr(p.case_id)}')">Delete</button>`;
    return `<tr>
      <td>${escapeHtml(p.case_number || p.case_id || '—')}</td>
      <td>${escapeHtml((p.involved_civilians || [])[0] || p.subject_name || '—')}</td>
      <td>${escapeHtml(({ case_packet: 'Case Packet', arrest_packet: 'Arrest Packet', warrant_packet: 'Warrant Packet', traffic_packet: 'Traffic Stop Packet', court_packet: 'Court Packet', gang_packet: 'Gang Investigation Packet' }[p.type] || p.type || 'Case Packet'))}</td>
      <td>${escapeHtml(p.linked_arrest_id || '—')}</td>
      <td>${escapeHtml(p.linked_warrant_id || '—')}</td>
      <td>${escapeHtml(p.linked_traffic_stop_id || p.traffic_stop_id || '—')}</td>
      <td>${escapeHtml(p.created_by || '—')}</td>
      <td>${formatDate(p.created_at)}</td>
      <td><span class="badge badge-secondary">${escapeHtml(({ open: 'Open', archived: 'Archived', closed: 'Closed' }[status] || status))}</span></td>
      <td class="table-actions">${viewButton} ${downloadButton} ${evidenceButton} ${deleteButton}</td>
    </tr>`;
  }).join('');
}
window.renderCasePacketsTable = renderCasePacketsTable;
function ensureCasePacketViewerModal() {
  let modal = document.getElementById('case-packet-viewer-modal');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'case-packet-viewer-modal';
  modal.className = 'case-packet-viewer-modal hidden';
  modal.innerHTML = `
    <div class="case-packet-viewer-backdrop" data-close-case-packet-viewer="true"></div>
    <div class="case-packet-viewer-panel" role="dialog" aria-modal="true" aria-label="Case Packet Viewer">
      <div class="case-packet-viewer-header">
        <h3>Case Packet Viewer</h3>
        <button type="button" class="button button-ghost" data-close-case-packet-viewer="true">Close</button>
      </div>
      <div class="case-packet-viewer-content" id="case-packet-viewer-content"></div>
      <div class="case-packet-viewer-actions" id="case-packet-viewer-actions"></div>
    </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click', (event) => {
    if (event.target?.dataset?.closeCasePacketViewer === 'true') closeCasePacketViewer();
  });
  return modal;
}

function closeCasePacketViewer() {
  const modal = document.getElementById('case-packet-viewer-modal');
  if (!modal) return;
  modal.classList.add('hidden');
  document.body.classList.remove('modal-open');
}
if (!window.__casePacketViewerEscBound) {
  window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeCasePacketViewer();
  });
  window.__casePacketViewerEscBound = true;
}

function openCasePacketViewer(packet, linkedEvidence) {
  const modal = ensureCasePacketViewerModal();
  const content = document.getElementById('case-packet-viewer-content');
  const actions = document.getElementById('case-packet-viewer-actions');
  if (!content || !actions) return;
  content.innerHTML = `
    <p><strong>Case ID:</strong> ${escapeHtml(packet.case_id || packet.case_number || '—')}</p>
    <p><strong>Packet Type:</strong> ${escapeHtml(packet.type || 'case_packet')}</p>
    <p><strong>Subject / Suspect:</strong> ${escapeHtml((packet.involved_civilians || [])[0] || packet.subject_name || '—')}</p>
    <p><strong>Created By:</strong> ${escapeHtml(packet.created_by || '—')}</p>
    <p><strong>Created Date:</strong> ${formatDate(packet.created_at)}</p>
    <p><strong>Status:</strong> ${escapeHtml(packet.status || 'open')}</p>
    <p><strong>Linked Arrest ID:</strong> ${escapeHtml(packet.linked_arrest_id || '—')}</p>
    <p><strong>Linked Warrant ID:</strong> ${escapeHtml(packet.linked_warrant_id || '—')}</p>
    <p><strong>Linked Traffic Stop ID:</strong> ${escapeHtml(packet.linked_traffic_stop_id || '—')}</p>
    <p><strong>Charges / Basis:</strong> ${escapeHtml(packet.charges || '—')}</p>
    <p><strong>Narrative / Summary:</strong> ${escapeHtml(packet.report_notes || '—')}</p>
    <p><strong>Evidence links/counts:</strong> ${escapeHtml(linkedEvidence.join(', ') || 'None')}</p>
    <p><strong>Court/hearing info:</strong> ${escapeHtml(packet.court_info || 'Missing')}</p>
    <p><strong>Packet notes/metadata:</strong> ${escapeHtml(packet.title || '—')}</p>`;
  actions.innerHTML = `${packet.download_url ? `<a class="button button-secondary" href="${escapeAttr(packet.download_url)}">Download PDF</a>` : ''}
    ${linkedEvidence[0] ? `<button class="button button-secondary" data-open-case-packet-evidence="true">Open Evidence${linkedEvidence.length > 1 ? ` (${linkedEvidence.length})` : ''}</button>` : ''}
    <button class="button button-ghost" data-delete-case-packet="true">Delete / Archive</button>
    <button class="button button-ghost" data-close-case-packet-viewer="true">Close</button>`;
  actions.querySelector('[data-open-case-packet-evidence="true"]')?.addEventListener('click', () => focusEvidenceItem(linkedEvidence[0]));
  actions.querySelector('[data-delete-case-packet="true"]')?.addEventListener('click', async () => {
    await deleteCasePacket(packet.case_id || packet.case_number || '');
    closeCasePacketViewer();
  });
  modal.classList.remove('hidden');
  document.body.classList.add('modal-open');
}

window.viewCasePacket = async (caseId) => {
  if (!caseId) return;
  const res = await fetch(`/api/cad/cases/${encodeURIComponent(caseId)}`);
  const data = await res.json();
  if (!res.ok || !data.success) throw new Error(data.error || 'Case packet view failed');
  const packet = data.case || {};
  const linkedEvidence = Array.isArray(packet.linked_evidence_ids) ? packet.linked_evidence_ids : String(packet.linked_evidence_ids || '').split(',').map((v) => v.trim()).filter(Boolean);
  openCasePacketViewer(packet, linkedEvidence);
};
window.deleteCasePacket = async (caseId) => {
  if (!caseId || !window.confirm('Archive this case packet?')) return;
  const res = await fetch(`/api/cad/case-packets/${encodeURIComponent(caseId)}`, { method: 'DELETE', credentials: 'include' });
  const data = await res.json();
  if (!res.ok || !data.success) throw new Error(data.error || 'Case packet delete failed');
  requestDataRefresh();
  renderCasePacketsTable();
};
window.focusEvidenceItem = (itemId) => {
  document.querySelector('[data-cad-module-target="evidence"]')?.click();
  const safeId = String(itemId || '').trim();
  if (!safeId) {
    showToast('Linked evidence was not found in the current Evidence Lock-Up view.', 'error');
    return;
  }
  const escapedId = (window.CSS && typeof window.CSS.escape === 'function') ? window.CSS.escape(safeId) : safeId.replace(/["\\]/g, '\\$&');
  const selector = `#evidence-tbody tr[data-evidence-id="${escapedId}"], #evidence-tbody tr[data-attachment-id="${escapedId}"], #evidence-tbody tr[data-evidence-attachment-id="${escapedId}"]`;
  const row = document.querySelector(selector);
  if (!row) {
    showToast('Linked evidence was not found in the current Evidence Lock-Up view.', 'error');
    return;
  }
  row.scrollIntoView({ behavior: 'smooth', block: 'center' });
  row.classList.add('evidence-highlight', 'case-packet-highlight');
  window.setTimeout(() => row.classList.remove('evidence-highlight', 'case-packet-highlight'), 3000);
};

function initCadMapFilters() {
  document.querySelectorAll('.cad-map-filter').forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.mapTarget;
      const filter = btn.dataset.mapFilter || 'all';
      document.querySelectorAll(`.cad-map-filter[data-map-target="${target}"]`).forEach((b) => b.classList.toggle('active', b === btn));
      const canvas = document.querySelector(`.cad-map-canvas[data-map-target="${target}"]`);
      if (!canvas) return;
      canvas.querySelectorAll('.cad-map-marker').forEach((marker) => {
        const cat = String(marker.dataset.mapCategory || '');
        marker.style.display = (filter === 'all' || cat.includes(filter)) ? '' : 'none';
      });
    });
  });
}

function trafficOutcomeTemplate(outcome) {
  if (outcome === 'Citation') return `<h4>Citation / Ticket</h4><div class="form-grid"><label><span>Violation</span><input name="citationViolation" placeholder="Speeding / reckless driving"></label><label><span>Citation amount</span><input name="citationAmount" placeholder="250"></label><label><span>Court required</span><select name="citationCourtRequired"><option>No</option><option>Yes</option></select></label><label><span>Court date</span><input name="citationCourtDate" type="datetime-local"></label></div><label><span>Ticket notes</span><textarea name="citationNotes" rows="3"></textarea></label><div class="actions"><button type="button" onclick="aiCompleteTrafficOutcome('Citation')">AI Complete Citation</button><button type="button" onclick="generateTrafficOutcomePdf('citation')">Generate Ticket PDF</button></div>`;
  if (outcome === 'Warning') return `<h4>Warning</h4><div class="form-grid"><label><span>Warning reason</span><input name="warningReason"></label><label><span>Warning type</span><input name="warningType" placeholder="Verbal / Written"></label></div><label><span>Warning notes</span><textarea name="warningNotes" rows="3"></textarea></label><div class="actions"><button type="button" onclick="aiCompleteTrafficOutcome('Warning')">AI Complete Warning</button><button type="button" onclick="generateTrafficOutcomePdf('warning')">Generate Warning PDF</button></div>`;
  if (outcome === 'Arrest') return `<h4>Arrest Transition</h4><label><span>Charges</span><textarea name="arrestCharges" rows="2"></textarea></label><label><span>Probable cause / narrative</span><textarea name="arrestNarrative" rows="3"></textarea></label><label><span>Jail / fine if applicable</span><input name="arrestPenalty"></label><div class="actions"><button type="button" onclick="createArrestFromTrafficStop()">Create Arrest Report From Stop</button><button type="button" onclick="aiCompleteTrafficOutcome('Arrest')">AI Complete Arrest Report</button><button type="button" onclick="bookJailFromTrafficStop()">Book/Jail Suspect</button><button type="button" onclick="createCourtDateFromTrafficStop()">Create Court Date</button><button type="button" onclick="generateCasePacketFromTrafficStop()">Generate Case Packet</button></div>`;
  return '';
}

function bindTrafficOutcomeFlow(form) {
  const select = form.querySelector('[name="trafficOutcome"]');
  const panel = document.getElementById('traffic-outcome-flow');
  if (!select || !panel) return;
  const render = () => {
    panel.innerHTML = trafficOutcomeTemplate(select.value);
    panel.classList.toggle('hidden', !select.value);
  };
  select.onchange = render;
  render();
}

function trafficAiEndpoint(outcome) {
  if (outcome === 'Citation') return '/api/cad/ai/traffic-citation';
  if (outcome === 'Warning') return '/api/cad/ai/traffic-warning';
  return '/api/cad/ai/traffic-arrest';
}

function fillIfEmpty(form, fieldName, value) {
  if (!form?.[fieldName] || value === undefined || value === null || value === '') return;
  if (!String(form[fieldName].value || '').trim()) form[fieldName].value = value;
}

async function aiCompleteTrafficOutcome(outcome) {
  const form = document.getElementById('traffic-form');
  if (!form) return;
  try {
    const res = await fetch(trafficAiEndpoint(outcome), { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(getFormData(form)) });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || 'CAD AI request failed');
    const s = data.suggestions || data;
    if (outcome === 'Citation') {
      fillIfEmpty(form, 'citationViolation', s.violation);
      fillIfEmpty(form, 'citationAmount', s.citation_amount || s.citationAmount);
      fillIfEmpty(form, 'citationCourtRequired', s.court_required || s.courtRequired);
      fillIfEmpty(form, 'citationCourtDate', s.court_date || s.courtDate);
      fillIfEmpty(form, 'citationNotes', s.notes);
    } else if (outcome === 'Warning') {
      fillIfEmpty(form, 'warningReason', s.warning_reason || s.warningReason);
      fillIfEmpty(form, 'warningType', s.warning_type || s.warningType);
      fillIfEmpty(form, 'warningNotes', s.notes);
    } else {
      fillIfEmpty(form, 'arrestCharges', s.charges);
      fillIfEmpty(form, 'arrestNarrative', s.arrest_narrative || s.probable_cause || s.probableCause);
      fillIfEmpty(form, 'arrestPenalty', s.jail_recommendation || s.jailRecommendation);
    }
    showToast(`${outcome} AI suggestions filled for review`, 'success');
  } catch (err) { showToast(err.message || 'CAD AI request failed', 'error'); }
}

async function ensureTrafficStopSaved() {
  const form = document.getElementById('traffic-form');
  if (!form) throw new Error('Traffic stop form not found');
  const currentId = form.dataset.trafficStopId;
  const data = getFormData(form);
  if (currentId) data.id = currentId;
  const stop = await addTrafficStop(data);
  form.dataset.trafficStopId = stop.id || stop.stop_id || stop.traffic_stop_id;
  return { stop, data: getFormData(form), id: form.dataset.trafficStopId };
}

async function generateTrafficOutcomePdf(type) {
  try {
    const { id, data } = await ensureTrafficStopSaved();
    const route = type === 'warning' ? 'warning-pdf' : 'citation-pdf';
    const res = await fetch(`/api/cad/traffic-stops/${encodeURIComponent(id)}/${route}`, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
    const out = await res.json();
    if (!res.ok || !out.success) throw new Error(out.error || 'Traffic PDF generation failed');
    showToast(`Traffic PDF generated: ${out.download_url}`, 'success');
    if (out.download_url) window.open(out.download_url, '_blank', 'noopener');
  } catch (err) { showToast(err.message || 'Traffic PDF generation failed', 'error'); }
}

async function createArrestFromTrafficStop() {
  try {
    const { id, data } = await ensureTrafficStopSaved();
    const res = await fetch(`/api/cad/traffic-stops/${encodeURIComponent(id)}/create-arrest`, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
    const out = await res.json();
    if (!res.ok || !out.success) throw new Error(out.error || 'Unable to create arrest report');
    requestDataRefresh();
    renderArrestsTable();
    showToast(`${out.created ? 'Arrest report created' : 'Existing arrest report opened'}: ${out.arrest?.id || out.arrest?.arrest_id}`, 'success');
    return out.arrest;
  } catch (err) { showToast(err.message || 'Unable to create arrest report', 'error'); }
}

async function bookJailFromTrafficStop() {
  if (!confirm('Book/Jail this suspect for the selected traffic stop?')) return;
  try {
    const { id, data } = await ensureTrafficStopSaved();
    const res = await fetch(`/api/cad/traffic-stops/${encodeURIComponent(id)}/book-jail`, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
    const out = await res.json();
    if (!res.ok || !out.success) throw new Error(out.error || 'Unable to book suspect');
    if (typeof loadJail === 'function') await loadJail();
    showToast('Suspect booked/jailed for this stop', 'success');
  } catch (err) { showToast(err.message || 'Unable to book suspect', 'error'); }
}

async function createCourtDateFromTrafficStop() {
  if (!confirm('Create a court date for this traffic stop arrest?')) return;
  try {
    const { id, data } = await ensureTrafficStopSaved();
    const res = await fetch(`/api/cad/traffic-stops/${encodeURIComponent(id)}/court-date`, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
    const out = await res.json();
    if (!res.ok || !out.success) throw new Error(out.error || 'Unable to create court date');
    if (typeof loadCourtHearings === 'function') await loadCourtHearings();
    showToast(`Court date created: ${out.hearing?.id || out.hearing?.hearing_id}`, 'success');
  } catch (err) { showToast(err.message || 'Unable to create court date', 'error'); }
}

async function generateCasePacketFromTrafficStop() {
  try {
    const { id } = await ensureTrafficStopSaved();
    await generateCasePacket({ title: 'Traffic Stop Case Packet', traffic_stop_id: id });
  } catch (err) { showToast(err.message || 'Case packet failed', 'error'); }
}

function handleTrafficForm() {
  const form = document.getElementById('traffic-form');
  if (!form) return;
  bindTrafficOutcomeFlow(form);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const submit = form.querySelector('[type="submit"]');
    if (submit) submit.disabled = true;
    try {
      const stop = await addTrafficStop(getFormData(form));
      form.dataset.trafficStopId = stop.id || stop.stop_id || stop.traffic_stop_id;
      updateDashboard();
      renderTrafficTable();
      addActivity('Traffic Stop', `Traffic stop logged for ${stop.driverName || form.driverName?.value} (${stop.trafficPlate || stop.plate || form.trafficPlate?.value})`);
      showToast('Traffic stop logged successfully', 'success');
    } catch (err) {
      showToast(err.message || 'Traffic stop save failed', 'error');
    } finally {
      if (submit) submit.disabled = false;
    }
  });
}

function handleArrestForm() {
  const form = document.getElementById('arrest-form');
  if (!form) return;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const data = getFormData(form);
    const submitButton = form.querySelector('[type="submit"]');
    if (submitButton) submitButton.disabled = true;
    try {
      const arrest = await addArrest(data);
      updateDashboard();
      renderArrestsTable();
      addActivity('Arrest Report', `Arrest filed for ${arrest.suspectName || data.suspectName} - ${arrest.charges || data.charges}`);
      showToast('Arrest report filed successfully', 'success');
      if (typeof loadCourtHearings === 'function') await loadCourtHearings();
      if (typeof loadJail === 'function') await loadJail();
      const recordInput = document.getElementById('criminal-record-input');
      if (recordInput && recordInput.value.trim()) document.getElementById('criminal-record-btn')?.click();
      form.reset();
    } catch (err) {
      showToast(err.message || 'Arrest report save failed', 'error');
    } finally {
      if (submitButton) submitButton.disabled = false;
    }
  });
}

function handleEvidenceForm() {
  const form = document.getElementById('evidence-form');
  if (!form) return;
  const fileInput = form.querySelector('input[name="file"]');
  const hint = form.querySelector('[data-evidence-upload-hint]');
  const config = GTAVCADData.evidenceAttachmentConfig || {};
  if (fileInput && config.direct_uploads_enabled !== true) {
    fileInput.disabled = true;
    fileInput.closest('label')?.classList.add('is-disabled');
    if (hint) hint.textContent = config.direct_upload_message || 'Direct uploads are not configured. Attach an external evidence link instead.';
  } else if (hint) {
    hint.textContent = 'Attach an external evidence link or upload an allowed evidence file.';
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const data = getFormData(form);
    const submitButton = form.querySelector('button[type="submit"]');
    if (submitButton) submitButton.disabled = true;
    showFormMessage(form, 'Saving evidence attachment…', 'info');
    try {
      const attachment = await addEvidence(data, form);
      updateDashboard();
      renderEvidenceTable();
      const resolvedCase = attachment?.generated_case_number || attachment?.case_id || data.caseNumber || data.case_id || 'auto-generated case';
      addActivity('Evidence', `Evidence submitted for case ${resolvedCase}`);
      showToast('Evidence attachment submitted successfully', 'success');
      showFormMessage(form, `Evidence attachment submitted successfully${resolvedCase ? ` for case ${resolvedCase}` : ''}`, 'success');
      form.reset();
    } catch (err) {
      const message = err.message || 'Evidence attachment save failed';
      showToast(message, 'error');
      showFormMessage(form, message, 'error');
    } finally {
      if (submitButton) submitButton.disabled = false;
    }
  });
}

function updateWarrantTypeFields() {
  const form = document.getElementById('warrant-form');
  if (!form) return;
  const selectedType = form.querySelector('[name="warrant_type"]')?.value || 'Arrest Warrant';
  form.querySelectorAll('.warrant-type-field').forEach((field) => {
    const allowed = (field.dataset.warrantTypes || '').split(',').map(v => v.trim());
    const visible = allowed.includes(selectedType);
    field.style.display = visible ? '' : 'none';
    field.querySelectorAll('input, textarea, select').forEach(input => { if (!visible) input.value = ''; });
  });
}

function handleWarrantForm() {
  const form = document.getElementById('warrant-form');
  if (!form) return;
  form.querySelector('[name="warrant_type"]')?.addEventListener('change', updateWarrantTypeFields);
  updateWarrantTypeFields();
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const submitButton = form.querySelector('button[type="submit"]');
    try {
      if (submitButton) submitButton.disabled = true;
      showFormMessage(form, 'Creating warrant…', 'info');
      const data = getFormData(form);
      const warrant = await addWarrant(data);
      await requestDataRefresh();
      updateDashboard();
      renderWarrantsTable();
      addActivity('Warrant', `Warrant issued for ${warrant.subject_name || data.subject_name} - ${warrant.charges_or_basis || data.charges_or_basis}`);
      showToast('Warrant added successfully', 'success');
      showFormMessage(form, `Warrant ${warrant.warrant_number || warrant.id} created successfully.`, 'success');
      form.reset();
      updateWarrantTypeFields();
    } catch (err) {
      showToast(err.message || 'Warrant save failed', 'error');
      showFormMessage(form, err.message || 'Warrant save failed', 'error');
    } finally {
      if (submitButton) submitButton.disabled = false;
    }
  });
}

function handleCivilianLookupForm() {
  const form = document.getElementById('civilian-lookup-form');
  if (!form) return;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const nameQuery = form.querySelector('[name="lookupName"]').value.trim();
    const dobQuery = (form.querySelector('[name="lookupDob"]')?.value || '').trim();
    const licenseQuery = (form.querySelector('[name="lookupLicense"]')?.value || '').trim();
    const query = [nameQuery, licenseQuery].filter(Boolean).join(' ').trim();
    const resultsContainer = document.getElementById('civilian-lookup-results');
    const statusEl = document.getElementById('civilian-lookup-status');

    if ((!query || query.length < 2) && !dobQuery) {
      if (statusEl) { statusEl.textContent = 'Enter at least 2 characters or a DOB to search.'; statusEl.className = 'form-status error'; }
      return;
    }

    if (statusEl) { statusEl.textContent = 'Searching database…'; statusEl.className = 'form-status'; }

    try {
      const res = await fetch('/api/civilian/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, name: nameQuery, dob: dobQuery }),
      });
      const data = await res.json();

      if (data.success) {
        const results = data.results || [];
        const mapped = results.map(r => ({
          id: r.civilian_id || r.id,
          firstName: r.firstName || r.first_name || '',
          lastName: r.lastName || r.last_name || '',
          phone: r.phone || r.phone_number || '',
          address: r.address || '',
          discord: '',
          dob: r.dob || r.date_of_birth || '',
          occupation: r.occupation || '',
          driverLicense: r.driverLicense || r.driver_license_status || '',
          firearmLicense: r.firearmLicense || r.firearm_license_status || '',
          businessLicense: r.businessLicense || r.business_license_status || '',
          vehicleMake: r.vehicleMake || r.vehicle_make || '',
          vehicleModel: r.vehicleModel || r.vehicle_model || '',
          vehicleYear: r.vehicleYear || r.vehicle_year || '',
          vehicleColor: r.vehicleColor || r.vehicle_color || '',
          plate: r.plate || r.plate_number || '',
          insuranceStatus: r.insurance || r.insurance_status || '',
          hasCriminalHistory: Boolean(r.hasCriminalHistory),
          criminalNotes: r.background || r.criminal_background_notes || (r.hasCriminalHistory ? 'Criminal record present. See related records below.' : 'No criminal history on file'),
        }));
        renderLookupResults(resultsContainer, mapped, 'civilian');
        addActivity('Civilian Lookup', `Civilian lookup performed for "${query}"`);
        showToast(`Found ${results.length} civilian record(s)`, 'info');
        if (statusEl) { statusEl.textContent = `Found ${results.length} record(s)`; statusEl.className = 'form-status success'; }
      } else {
        if (resultsContainer) resultsContainer.innerHTML = `<div class="result-card"><p style="color:var(--accent);">${data.error || 'Search failed'}</p></div>`;
        if (statusEl) { statusEl.textContent = data.error || 'Search failed'; statusEl.className = 'form-status error'; }
      }
    } catch (err) {
      if (resultsContainer) resultsContainer.innerHTML = `<div class="result-card"><p style="color:var(--accent);">Network error: ${err.message}</p></div>`;
      if (statusEl) { statusEl.textContent = `Network error: ${err.message}`; statusEl.className = 'form-status error'; }
    }
  });
}

function handlePlateLookupForm() {
  const form = document.getElementById('plate-lookup-form');
  if (!form) return;
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const plate = form.querySelector('[name="plateLookup"]').value;
    const results = lookupVehiclePlate(plate);
    renderLookupResults(document.getElementById('plate-lookup-results'), results, 'vehicle');
    addActivity('Vehicle Lookup', `Vehicle lookup performed for plate "${plate}"`);
    showToast(`Found ${results.length} vehicle record(s)`, 'info');
  });
}

function handleLicenseForm() {
  const form = document.getElementById('license-form');
  if (!form) return;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const statusEl = document.getElementById('license-status');
    const submitButton = form.querySelector('button[type="submit"]');
    
    try {
      submitButton.disabled = true;
      statusEl.textContent = 'Submitting...';
      statusEl.style.color = 'var(--muted)';
      statusEl.style.display = 'block';
      
      const data = getFormData(form);
      const result = await addLicense(data);
      
      statusEl.textContent = 'License application submitted successfully!';
      statusEl.style.color = '#4caf50';
      showToast('License submitted successfully', 'success');
      form.reset();
    } catch (error) {
      statusEl.textContent = `Error: ${error.message}`;
      statusEl.style.color = '#ff6b6b';
      showToast(`License submission failed: ${error.message}`, 'error');
    } finally {
      submitButton.disabled = false;
    }
  });
}

function handleVehicleForm() {
  const form = document.getElementById('vehicle-form');
  if (!form) return;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const statusEl = document.getElementById('vehicle-status');
    const submitButton = form.querySelector('button[type="submit"]');
    
    try {
      submitButton.disabled = true;
      statusEl.textContent = 'Submitting...';
      statusEl.style.color = 'var(--muted)';
      statusEl.style.display = 'block';
      
      const data = getFormData(form);
      const result = await addVehicle(data);
      
      statusEl.textContent = 'Vehicle registered successfully!';
      statusEl.style.color = '#4caf50';
      showToast('Vehicle registered successfully', 'success');
      form.reset();
    } catch (error) {
      statusEl.textContent = `Error: ${error.message}`;
      statusEl.style.color = '#ff6b6b';
      showToast(`Vehicle registration failed: ${error.message}`, 'error');
    } finally {
      submitButton.disabled = false;
    }
  });
}

function handleDMVPlateForm() {
  const form = document.getElementById('dmv-plate-form');
  if (!form) return;
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const plate = form.querySelector('[name="plateSearch"]').value;
    const results = lookupVehiclePlate(plate);
    renderLookupResults(document.getElementById('dmv-plate-results'), results, 'vehicle');
    document.dispatchEvent(new CustomEvent('gtavcad:dmv-results', { detail: { count: Array.isArray(results) ? results.length : 0 } }));
    showFormMessage(form, `Found ${results.length} vehicle record(s).`);
  });
}

// PHASE 1: Business persistence via dedicated /api/businesses route
async function createBusiness(record) {
  try {
    const payload = {
      businessName: record.businessName || record.name,
      businessType: record.businessType || record.type,
      licenseStatus: record.licenseStatus || 'Active',
      address: record.desiredLocation || record.address || '',
      ownerCivilianId: record.ownerCivilianId || '',
      employees: parseInt(record.employees) || 0,
      inspectionNotes: record.inspectionNotes || '',
      legalFlags: record.illegalDisclosure || record.legalFlags || '',
    };
    
    const res = await fetch('/api/businesses', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || !data.success) {
      throw new Error(data.error || 'Business registration failed');
    }
    // Refresh data from backend after success
    requestDataRefresh();
    return data.business;
  } catch (error) {
    console.error('Business registration error:', error);
    throw error;
  }
}

function handleBusinessForm() {
  const form = document.getElementById('business-form');
  if (!form) return;
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const statusEl = document.getElementById('business-status');
    const submitButton = form.querySelector('button[type="submit"]');
    
    try {
      submitButton.disabled = true;
      statusEl.textContent = 'Processing business request...';
      statusEl.style.color = 'var(--muted)';
      statusEl.style.display = 'block';
      
      const data = getFormData(form);
      const result = await createBusiness(data);
      
      statusEl.textContent = 'Business request submitted successfully! Staff will review it shortly.';
      statusEl.style.color = '#4caf50';
      showToast('Business request submitted successfully', 'success');
      form.reset();
    } catch (error) {
      statusEl.textContent = `Error: ${error.message}`;
      statusEl.style.color = '#ff6b6b';
      showToast(`Business submission failed: ${error.message}`, 'error');
    } finally {
      submitButton.disabled = false;
    }
  });
}

// Map filter behavior
const filterButtons = document.querySelectorAll('.filter-btn');
const pins = document.querySelectorAll('.map-pin');

filterButtons.forEach((button) => {
  button.addEventListener('click', () => {
    filterButtons.forEach((item) => item.classList.remove('active'));
    button.classList.add('active');
    const filter = button.dataset.filter;

    pins.forEach((pin) => {
      if (filter === 'all') {
        pin.style.display = 'inline-flex';
      } else {
        pin.style.display = pin.dataset.category === filter ? 'inline-flex' : 'none';
      }
    });
  });
});

// Map pin click handlers
pins.forEach((pin) => {
  pin.addEventListener('click', () => {
    const location = pin.dataset.location;
    showMapDetails(location);
  });
});

// Warrant filter handlers
const warrantFilterButtons = document.querySelectorAll('.warrants-panel .filter-btn');
warrantFilterButtons.forEach((button) => {
  button.addEventListener('click', () => {
    warrantFilterButtons.forEach((item) => item.classList.remove('active'));
    button.classList.add('active');
    const filter = button.dataset.filter;
    renderWarrantsTable(filter);
  });
});

// Map details
function showMapDetails(location) {
  const detailsContainer = document.getElementById('map-details');
  if (!detailsContainer) return;

  const locationData = getLocationData(location);
  if (!locationData) return;

  detailsContainer.innerHTML = `
    <div class="location-header">
      <div class="location-name">${locationData.name}</div>
      <div class="location-category badge badge-primary">${locationData.category}</div>
    </div>
    <div class="location-info">
      <div><strong>Purpose:</strong> ${locationData.purpose}</div>
      ${locationData.discord ? `<div class="location-discord">${locationData.discord}</div>` : ''}
    </div>
  `;
}

function getLocationData(location) {
  const locations = {
    'police-dept': {
      name: 'Police Department',
      category: 'Police',
      purpose: 'Officer staging, reports, booking, evidence processing.',
      discord: '#police-evidence-lock-up'
    },
    'dmv': {
      name: 'DMV',
      category: 'Government',
      purpose: 'Vehicle registration, license issuance, and civilian services.',
      discord: '#dmv-services'
    },
    'court': {
      name: 'Court / City Hall',
      category: 'Government',
      purpose: 'Legal proceedings, city administration, and public services.',
      discord: '#court-proceedings'
    },
    'hospital': {
      name: 'Hospital / EMS',
      category: 'Emergency',
      purpose: 'Medical treatment, emergency response, and healthcare services.',
      discord: '#ems-dispatch'
    },
    'dealership': {
      name: 'Dealership',
      category: 'Business',
      purpose: 'Vehicle sales, maintenance, and automotive services.',
      discord: '#business-services'
    },
    'bank': {
      name: 'Bank',
      category: 'Business',
      purpose: 'Financial services, loans, and banking operations.',
      discord: '#business-services'
    },
    'gang-territory': {
      name: 'Gang Territory',
      category: 'Criminal',
      purpose: 'High-crime area requiring increased police presence.',
      discord: '#gang-activity'
    },
    'business-hub': {
      name: 'Business Hub',
      category: 'Business',
      purpose: 'Commercial district with multiple businesses and services.',
      discord: '#business-services'
    },
    'jail': {
      name: 'Jail',
      category: 'Police',
      purpose: 'Detention facility for arrested individuals and prisoner processing.',
      discord: '#jail-processing'
    }
  };

  return locations[location];
}


function isCivilianDashboardPage() {
  return document.body?.dataset.dashboardPage === 'civilian';
}

function getCivilianDashboardUrl(civilianId = '') {
  const base = CURRENT_COMMUNITY_SLUG ? `/c/${CURRENT_COMMUNITY_SLUG}/civilian-dashboard` : '/civilian-dashboard';
  return civilianId ? `${base}?civilian_id=${encodeURIComponent(civilianId)}` : base;
}

function renderKeyValueGrid(container, rows) {
  if (!container) return;
  container.innerHTML = rows.map(([label, value]) => `<div class="profile-row"><span class="profile-label">${escapeHtml(label)}:</span><span class="profile-value">${escapeHtml(value || '—')}</span></div>`).join('');
}

function renderDashboardEmpty(container, message) {
  if (!container) return;
  container.innerHTML = `<p class="record-empty">${escapeHtml(message)}</p>`;
}

function renderCivilianDashboardTable(container, columns, rows, emptyMessage) {
  if (!container) return;
  if (!rows || !rows.length) {
    renderDashboardEmpty(container, emptyMessage);
    return;
  }
  const head = columns.map((column) => `<th>${escapeHtml(column.label)}</th>`).join('');
  const body = rows.map((row) => `<tr>${columns.map((column) => `<td>${escapeHtml(row[column.key] ?? '')}</td>`).join('')}</tr>`).join('');
  container.innerHTML = `<div class="data-table-container"><table class="data-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function renderCivilianDashboard(data) {
  const status = document.getElementById('civilian-dashboard-status');
  const selectorCard = document.getElementById('civilian-profile-selector-card');
  const content = document.getElementById('civilian-dashboard-content');
  const profiles = data.profiles || [];
  if (!data.civilian) {
    if (content) content.classList.add('hidden');
    if (selectorCard) selectorCard.classList.toggle('hidden', profiles.length === 0);
    if (status) {
      if (profiles.length) {
        status.textContent = 'Choose Civilian for This Session';
      } else {
        const createUrl = CURRENT_COMMUNITY_SLUG ? `/c/${CURRENT_COMMUNITY_SLUG}/civilian.html` : '/civilian.html';
        status.innerHTML = `No civilian profiles are linked to your account in this community.<br><a class="button button-primary" href="${createUrl}">Create Civilian Profile</a>`;
      }
    }
    return;
  }
  if (status) status.classList.add('hidden');
  if (selectorCard) selectorCard.classList.toggle('hidden', profiles.length <= 1);
  if (content) content.classList.remove('hidden');

  const civilian = data.civilian || {};
  const badge = document.getElementById('civilian-license-badge');
  if (badge) badge.textContent = civilian.license_status || 'License';
  renderKeyValueGrid(document.getElementById('civilian-profile-card'), [
    ['Full name', civilian.name],
    ['DOB', civilian.date_of_birth],
    ['Phone', civilian.phone],
    ['Address', civilian.address],
    ['Occupation', civilian.occupation],
    ['License status', civilian.license_status],
    ['Community', civilian.community_name || window.GTAVCAD_CONTEXT?.communityName],
  ]);

  const summary = data.summary || {};
  const stats = document.getElementById('civilian-quick-stats');
  if (stats) {
    stats.innerHTML = [
      ['Unpaid tickets', summary.unpaid_tickets || 0],
      ['Open fines', summary.open_fines || 0],
      ['Served warrants', summary.served_warrants || 0],
      ['Upcoming court dates', summary.upcoming_court_dates || 0],
    ].map(([label, value]) => `<article class="dashboard-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></article>`).join('');
  }

  renderCivilianDashboardTable(document.getElementById('civilian-vehicles'), [
    { key: 'plate', label: 'Plate' }, { key: 'make', label: 'Make' }, { key: 'model', label: 'Model' },
    { key: 'color', label: 'Color' }, { key: 'registration_status', label: 'Registration' }, { key: 'insurance_status', label: 'Insurance' },
  ], data.vehicles || [], 'No registered vehicles on record.');
  renderCivilianDashboardTable(document.getElementById('civilian-licenses'), [
    { key: 'license_type', label: 'License type' }, { key: 'status', label: 'Status' }, { key: 'expiration', label: 'Expiration' }, { key: 'restrictions', label: 'Restrictions' },
  ], data.licenses || [], 'No licenses on record.');
  renderCivilianDashboardTable(document.getElementById('civilian-tickets-fines'), [
    { key: 'citation_number', label: 'Citation #' }, { key: 'violation', label: 'Violation' }, { key: 'amount', label: 'Amount' },
    { key: 'status', label: 'Status' }, { key: 'issued_date', label: 'Issued' }, { key: 'court_required', label: 'Court required' },
  ], data.citations || [], 'No tickets or fines on record.');
  renderCivilianDashboardTable(document.getElementById('civilian-arrest-jail'), [
    { key: 'arrest_date', label: 'Date' }, { key: 'charges', label: 'Charges' }, { key: 'status', label: 'Status' }, { key: 'jail_time', label: 'Jail/Fine' }, { key: 'public_notes', label: 'Public notes' },
  ], [...(data.arrests || []), ...(data.jail_history || [])], 'No arrest or jail history on record.');
  renderCivilianDashboardTable(document.getElementById('civilian-served-warrants'), [
    { key: 'warrant_number', label: 'Warrant #' }, { key: 'warrant_type', label: 'Type' }, { key: 'status', label: 'Status' },
    { key: 'served_date', label: 'Served' }, { key: 'charges_or_basis', label: 'Charges/Basis' }, { key: 'court_case_number', label: 'Court case' },
  ], data.served_warrants || [], 'No served warrants on record.');
  renderCivilianDashboardTable(document.getElementById('civilian-court-dates'), [
    { key: 'hearing_date', label: 'Hearing date' }, { key: 'courtroom', label: 'Courtroom' }, { key: 'case_number', label: 'Case #' }, { key: 'status', label: 'Status' }, { key: 'outcome', label: 'Outcome' },
  ], data.court_dates || [], 'No court dates on record.');
  renderCivilianDashboardTable(document.getElementById('civilian-complaints'), [
    { key: 'complaint_id', label: 'Complaint ID' }, { key: 'category', label: 'Category' }, { key: 'status', label: 'Status' }, { key: 'submitted_date', label: 'Submitted' },
  ], data.complaints || [], 'No complaints on record.');
}

function bindCivilianProfileSelector(profiles = []) {
  const selector = document.getElementById('civilian-profile-selector');
  const button = document.getElementById('civilian-profile-open');
  if (!selector || !button) return;
  selector.innerHTML = profiles.map((profile) => `<option value="${escapeAttr(profile.civilian_id)}">${escapeHtml(profile.name || profile.civilian_id)}</option>`).join('');
  button.onclick = () => {
    const civilianId = selector.value;
    if (civilianId) window.location.href = getCivilianDashboardUrl(civilianId);
  };
}

async function initCivilianDashboard() {
  if (!isCivilianDashboardPage()) return;
  const status = document.getElementById('civilian-dashboard-status');
  try {
    const params = new URLSearchParams(window.location.search);
    const civilianId = params.get('civilian_id') || '';
    const url = civilianId ? `/api/civilian/dashboard?civilian_id=${encodeURIComponent(civilianId)}` : '/api/civilian/dashboard';
    const res = await fetch(url, { credentials: 'include' });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || 'Unable to load Civilian Dashboard');
    bindCivilianProfileSelector(data.profiles || []);
    renderCivilianDashboard(data);
  } catch (error) {
    if (status) status.textContent = error.message || 'Unable to load Civilian Dashboard.';
  }
}


function renderGangModule(){
  const renderEmpty=(el,msg,colspan=1)=>{ if(!el) return; el.innerHTML=`<div class="cad-empty-state">${msg}</div>`; };
  const profilesEl=document.getElementById('gang-profiles-list');
  if(profilesEl){
    if(!(GTAVCADData.gangProfiles||[]).length) renderEmpty(profilesEl,'No gang profiles created yet.');
    else profilesEl.innerHTML=`<div class="cad-table-wrap"><table class="data-table"><thead><tr><th>Name</th><th>Territory</th><th>Threat</th><th>Status</th></tr></thead><tbody>${GTAVCADData.gangProfiles.map(g=>`<tr><td>${escapeHtml(g.gangName||'')}</td><td>${escapeHtml(g.territory||'—')}</td><td>${escapeHtml(g.threatLevel||'Low')}</td><td>${escapeHtml(g.status||'Active')}</td></tr>`).join('')}</tbody></table></div>`;
  }
  const casesEl=document.getElementById('gang-cases-list');
  if(casesEl){
    if(!(GTAVCADData.gangInvestigations||[]).length) renderEmpty(casesEl,'No gang investigation cases opened yet.');
    else casesEl.innerHTML=`<div class="cad-table-wrap"><table class="data-table"><thead><tr><th>Investigation ID</th><th>Title</th><th>Linked Gang</th><th>Status</th><th>Actions</th></tr></thead><tbody>${GTAVCADData.gangInvestigations.map(c=>`<tr><td>${escapeHtml(c.investigationId||'')}</td><td>${escapeHtml(c.caseTitle||'')}</td><td>${escapeHtml(c.linkedGang||'—')}</td><td>${escapeHtml(c.status||'Open')}</td><td class="table-actions"><button class="button button-secondary" data-gang-view="${escapeAttr(c.id)}">View</button><button class="button button-secondary" data-gang-evidence="${escapeAttr(c.id)}">Add Evidence</button><button class="button button-secondary" data-gang-packet="${escapeAttr(c.id)}">Generate Gang Investigation Packet</button><button class="button button-secondary" data-gang-close="${escapeAttr(c.id)}">Close Case</button></td></tr>`).join('')}</tbody></table></div>`;
  }
  const watchEl=document.getElementById('gang-watchlist-list');
  if(watchEl){
    if(!(GTAVCADData.gangWatchlist||[]).length) renderEmpty(watchEl,'No suspects currently on the gang watchlist.');
    else watchEl.innerHTML=`<div class="cad-table-wrap"><table class="data-table"><thead><tr><th>Suspect</th><th>Gang</th><th>Threat</th><th>Status</th><th>Links</th></tr></thead><tbody>${GTAVCADData.gangWatchlist.map(w=>`<tr><td>${escapeHtml(w.suspectName||'')}</td><td>${escapeHtml(w.gangAffiliation||'—')}</td><td>${escapeHtml(w.threatLevel||'Low')}</td><td>${escapeHtml(w.status||'Watching')}</td><td><button class="button button-secondary" data-watch-lookup="${escapeAttr(w.suspectName||'')}">Lookup Civilian</button><button class="button button-secondary" data-watch-warrants="${escapeAttr(w.suspectName||'')}">Warrants/Arrests</button></td></tr>`).join('')}</tbody></table></div>`;
  }
  const intelEl=document.getElementById('gang-intel-list');
  if(intelEl){
    if(!(GTAVCADData.gangIntelNotes||[]).length) renderEmpty(intelEl,'No intelligence notes recorded yet.');
    else intelEl.innerHTML=`<ul>${GTAVCADData.gangIntelNotes.map(n=>`<li><strong>${escapeHtml(n.noteId||'NOTE')}</strong> • ${escapeHtml(n.linkedGang||'Unlinked')} • ${escapeHtml(n.sourceReliability||'Unverified')}<br>${escapeHtml(n.note||'')}</li>`).join('')}</ul>`;
  }
  const packetEl=document.getElementById('gang-packet-list');
  if(packetEl){
    packetEl.innerHTML=(GTAVCADData.gangPackets||[]).map(p=>`<div class="cad-card"><strong>${escapeHtml(p.title)}</strong><div>Metadata Only — No PDF attached</div><div>AI-DRAFTED SECTION — OFFICER REVIEW REQUIRED</div></div>`).join('')||'<div class="cad-empty-state">No gang packets generated yet.</div>';
  }
}

function initGangModule(){
  const toggle=(btn,form)=>{const b=document.getElementById(btn),f=document.getElementById(form); if(b&&f){b.addEventListener('click',()=>f.classList.toggle('hidden')); f.addEventListener('submit',(e)=>{e.preventDefault();const rec=Object.fromEntries(new FormData(f).entries()); rec.id=generateId('gang'); rec.createdAt=new Date().toISOString(); if(form==='gang-profile-form') GTAVCADData.gangProfiles.unshift(rec); if(form==='gang-case-form'){ if(!rec.investigationId) rec.investigationId=generateId('INV'); GTAVCADData.gangInvestigations.unshift(rec);} if(form==='gang-watchlist-form') GTAVCADData.gangWatchlist.unshift(rec); if(form==='gang-intel-form'){ rec.noteId=generateId('NOTE'); rec.createdBy=(window.GTAVCAD_CURRENT_USER&&window.GTAVCAD_CURRENT_USER.username)||'Officer'; rec.createdDate=new Date().toISOString(); GTAVCADData.gangIntelNotes.unshift(rec);} saveData(); f.reset(); f.classList.add('hidden'); renderGangModule();});}};
  toggle('gang-profile-toggle','gang-profile-form');toggle('gang-case-toggle','gang-case-form');toggle('gang-watchlist-toggle','gang-watchlist-form');toggle('gang-intel-toggle','gang-intel-form');
  document.getElementById('generate-gang-packet')?.addEventListener('click',()=>{const packet={id:generateId('gangpkt'),title:'GANG INVESTIGATION PACKET PDF',type:'gang_packet',created_at:new Date().toISOString(),summary:'AI-DRAFTED SECTION — OFFICER REVIEW REQUIRED'}; GTAVCADData.gangPackets.unshift(packet); GTAVCADData.casePackets.unshift({...packet,case_id:packet.id,download_url:'',linked_evidence_ids:[]}); GTAVCADData.evidence.unshift({id:generateId('evd'),officer:'System',type:'GANG INVESTIGATION PACKET PDF',description:'Metadata Only — No PDF attached',link:'',createdAt:new Date().toISOString()}); saveData(); renderGangModule(); renderCasePacketsTable(); renderEvidenceTable(); showToast('Gang investigation packet generated (metadata only).','success');});
  document.addEventListener('click',(e)=>{const t=e.target; if(!(t instanceof HTMLElement)) return; if(t.dataset.gangClose){const rec=GTAVCADData.gangInvestigations.find(x=>x.id===t.dataset.gangClose); if(rec){rec.status='Closed'; saveData(); renderGangModule();}} if(t.dataset.watchLookup){document.querySelector('[data-cad-module-target="lookup"]')?.click(); const i=document.querySelector('#civilian-lookup-form [name="lookupName"]'); if(i){i.value=t.dataset.watchLookup; i.focus();}} if(t.dataset.watchWarrants){document.querySelector('[data-cad-module-target="warrants"]')?.click();}});
  renderGangModule();
}
// Initialize
async function initApp() {
  await applyCommunityBranding();
  if (document.body && document.body.dataset.platformPage === 'true') {
    setActiveNav();
    return;
  }
  if (isOfficerCadPage() && !enforceCadRoleVisibility()) {
    setActiveNav();
    return;
  }
  const shouldLoadCadData = isOfficerCadPage() && canAccessOfficerCad();
  if (shouldLoadCadData) requestDataRefresh();
  handleCivilianForm();
  handle911Form();
  handleTrafficForm();
  handleArrestForm();
  handleEvidenceForm();
  handleWarrantForm();
  handleCivilianLookupForm();
  handlePlateLookupForm();
  handleLicenseForm();
  handleVehicleForm();
  handleDMVPlateForm();
  handleBusinessForm();
  await initCivilianDashboard();

  // Initialize police CAD components only for authorized officer CAD pages.
  if (shouldLoadCadData) {
    updateDashboard();
    renderCallQueue();
    renderActivityFeed();
    renderWarrantsTable();
    renderArrestsTable();
    renderTrafficTable();
    renderEvidenceTable();
    renderCasePacketsTable();
    renderOfficersBoard();
    initCadMapFilters();
    initGangModule();
  }

  setActiveNav();
}

const setActiveNav = () => {
  const links = document.querySelectorAll('.global-nav a');
  const path = window.location.pathname;
  const leaf = window.location.pathname.split('/').pop();
  links.forEach((link) => {
    if (link.getAttribute('href') === path || link.getAttribute('href') === leaf || (leaf === '' && link.getAttribute('href') === 'index.html')) {
      link.classList.add('active-link');
    }
  });
};

initApp();

function showCommunityCreatedModal() {
  if (!CURRENT_COMMUNITY_SLUG || !window.sessionStorage) return;
  const rawPayload = sessionStorage.getItem('gtavcadCommunityCreated');
  if (!rawPayload) return;

  let payload;
  try {
    payload = JSON.parse(rawPayload);
  } catch (error) {
    sessionStorage.removeItem('gtavcadCommunityCreated');
    return;
  }

  if (!payload || payload.communitySlug !== CURRENT_COMMUNITY_SLUG) return;
  sessionStorage.removeItem('gtavcadCommunityCreated');

  const overlay = document.createElement('div');
  overlay.className = 'success-modal-overlay';
  overlay.setAttribute('role', 'dialog');
  overlay.setAttribute('aria-modal', 'true');
  overlay.setAttribute('aria-labelledby', 'community-created-title');

  const modal = document.createElement('section');
  modal.className = 'success-modal card';

  const eyebrow = createSafeElement('p', 'Community Created Successfully', 'eyebrow');
  const title = createSafeElement('h2', payload.communityName || 'New Community');
  title.id = 'community-created-title';

  const communityLabel = createSafeElement('p', 'Community:', 'success-modal-label');
  const communityName = createSafeElement('p', payload.communityName || 'Community', 'success-modal-value');
  const inviteLabel = createSafeElement('p', 'Invite Code:', 'success-modal-label');
  const inviteCode = createSafeElement('p', payload.inviteCode || 'Unavailable', 'invite-code-display');

  const actions = document.createElement('div');
  actions.className = 'hero-actions success-modal-actions';

  const enterCad = document.createElement('a');
  enterCad.className = 'button button-primary';
  enterCad.href = payload.redirectUrl || `/c/${CURRENT_COMMUNITY_SLUG}/`;
  enterCad.textContent = 'Enter CAD';

  const copyInvite = document.createElement('button');
  copyInvite.className = 'button button-secondary';
  copyInvite.type = 'button';
  copyInvite.textContent = 'Copy Invite Code';
  copyInvite.addEventListener('click', async () => {
    if (!payload.inviteCode || !navigator.clipboard) return;
    await navigator.clipboard.writeText(payload.inviteCode);
    copyInvite.textContent = 'Invite Code Copied';
  });

  const manage = document.createElement('a');
  manage.className = 'button button-ghost';
  manage.href = `/c/${CURRENT_COMMUNITY_SLUG}/cad`;
  manage.textContent = 'Manage Community';

  const close = document.createElement('button');
  close.className = 'modal-close-button';
  close.type = 'button';
  close.setAttribute('aria-label', 'Close success message');
  close.textContent = '×';
  close.addEventListener('click', () => overlay.remove());

  actions.append(enterCad, copyInvite, manage);
  modal.append(close, eyebrow, title, communityLabel, communityName, inviteLabel, inviteCode, actions);
  overlay.appendChild(modal);
  document.body.appendChild(overlay);
}

showCommunityCreatedModal();

window.GTAVCADData = GTAVCADData;

(function(){
  function cadInit(){
    const modules=[...document.querySelectorAll('.cad-module')]; if(!modules.length) return;
    const buttons=[...document.querySelectorAll('.cad-sidebar-btn')];
    const title=document.getElementById('cad-module-title');
    const feed=document.getElementById('cad-feed-items');
    const now=document.getElementById('cad-now');
    const status=document.getElementById('cad-officer-status');
    const key='cad.selectedModule';
    const labels={dashboard:'Dashboard',calls:'Active Calls',traffic:'Traffic Stops',lookup:'Lookup',warrants:'Warrants',arrests:'Arrests',evidence:'Evidence',reports:'Reports',court:'Court / Case Packets','officer-status':'Officer Status','gang-investigations':'Gang Unit / Investigations'};

    const mount=(name,selectors)=>{const host=document.querySelector(`[data-cad-mount="${name}"]`); if(!host) return; selectors.forEach(sel=>document.querySelectorAll(sel).forEach(el=>host.appendChild(el)));};
    mount('dashboard',['.cad-panel .container','.cad-panel .command-dashboard','#my-dashboard-section']);
    mount('calls',['.dispatch-section .call-queue-panel','#dispatch-form','.forms-full > .panel:nth-child(6)']);
    mount('traffic',['.dispatch-section .traffic-panel','#traffic-form','.forms-full > .panel:nth-child(7)']);
    mount('lookup',['#civilian-lookup-form','#plate-lookup-form','.lookup-results','.container.section:has(#criminal-record-input)']);
    mount('warrants',['.dispatch-section .warrants-panel','#warrant-form']);
    mount('arrests',['#arrest-form','.dispatch-section .officer-status-panel']);
    mount('evidence',['.dispatch-section .evidence-panel','#evidence-form']);
    mount('reports',['.container.section:has(#uof-generate-btn)']);
    mount('court',['.container.section:has(#case-packet-form)','.container.section:has(#court-hearings-list)']);
    mount('officer-status',['.dispatch-section .officer-status-panel']);

    document.querySelectorAll('main > section:not(.cad-shell)').forEach(sec=>{if(sec.closest('.cad-module')) return; if(sec.querySelector('[data-cad-mount]')) return; sec.classList.add('cad-legacy-hidden');});

    const role=(document.querySelector('[data-context-role]')?.textContent||'').trim().toLowerCase();
    const allowed=['platformowner','communityowner','communityadmin','owner','admin','police','officer','leo','detective','detectives','investigator','investigations','gang unit','doj','staff'];
    const gangBtn=document.querySelector('[data-cad-module-target="gang-investigations"]');
    if(gangBtn && role && !allowed.includes(role)) gangBtn.style.display='none';

    const addFeed=(badge,msg)=>{
      if(!feed) return;
      const d=document.createElement('div');
      d.className='cad-feed-item';
      const badgeEl=document.createElement('span');
      badgeEl.className=`cad-badge ${badge}`;
      badgeEl.textContent=new Date().toLocaleTimeString();
      d.appendChild(badgeEl);
      d.appendChild(document.createTextNode(` ${msg}`));
      feed.prepend(d);
    };
    const switchTo=(name)=>{modules.forEach(m=>m.classList.toggle('active',m.dataset.cadModule===name));buttons.forEach(b=>b.classList.toggle('active',b.dataset.cadModuleTarget===name));if(title) title.textContent=labels[name]||name; localStorage.setItem(key,name)};
    buttons.forEach(b=>b.addEventListener('click',()=>switchTo(b.dataset.cadModuleTarget)));
    document.querySelectorAll('[data-cad-quick-action]').forEach(btn=>btn.addEventListener('click',()=>{const m=btn.dataset.cadQuickAction;switchTo(m); const f=btn.dataset.cadFocus; if(f){const el=document.querySelector(f); if(el){el.scrollIntoView({behavior:'smooth',block:'center'});el.focus();}}}));
    ['dispatch-form','traffic-form','warrant-form','arrest-form','evidence-form'].forEach(id=>{const f=document.getElementById(id); if(f) f.addEventListener('submit',()=>addFeed('cad-badge-status-active',`${id.replace('-form','')} updated`));});
    if(status) status.addEventListener('change',()=>addFeed('cad-badge-status-pending',`Officer status changed to ${status.value}`));
    const saved=localStorage.getItem(key); if(saved&&labels[saved]) switchTo(saved);
    setInterval(()=>{if(now) now.textContent=new Date().toLocaleString();},1000);
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',cadInit); else cadInit();
})();
