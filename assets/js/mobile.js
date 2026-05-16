(function() {
  'use strict';

  var POLICE_ROLES = ['Police', 'LEO', 'Dispatch', 'EMS', 'DOJ', 'Staff', 'Admin', 'Owner'];

  function getCurrentPage() {
    var path = window.location.pathname.toLowerCase();
    var leaf = path.split('/').filter(Boolean).pop() || '';
    if (!leaf || leaf === 'index.html') return 'home';
    var map = {
      'cad': 'cad', 'police': 'police', 'police.html': 'police',
      'dmv': 'dmv', 'dmv.html': 'dmv', 'businesses': 'businesses', 'businesses.html': 'businesses',
      'applications': 'applications', 'applications.html': 'applications',
      'complaints': 'complaints', 'complaints.html': 'complaints',
      'donations': 'donations', 'donations.html': 'donations',
      'rules': 'rules', 'rules.html': 'rules',
      'community-admin': 'community-admin', 'community-admin.html': 'community-admin',
      'communities': 'communities', 'communities.html': 'communities',
      'admin': 'admin', 'admin.html': 'admin',
      'civilian': 'dispatch', 'civilian.html': 'dispatch', 'join': 'dispatch', 'join.html': 'dispatch',
      'login': 'login', 'register': 'register'
    };
    return map[leaf] || 'home';
  }

  function buildCommunityHref(page) {
    var slug = (window.GTAVCAD_CONTEXT && window.GTAVCAD_CONTEXT.communitySlug) || '';
    var pageMap = slug ? {
      'home': '/c/' + slug + '/',
      'cad': '/c/' + slug + '/cad',
      'police': '/c/' + slug + '/police.html',
      'dmv': '/c/' + slug + '/dmv.html',
      'dispatch': '/c/' + slug + '/civilian.html',
      'businesses': '/c/' + slug + '/businesses.html',
      'applications': '/c/' + slug + '/applications.html',
      'complaints': '/c/' + slug + '/complaints.html',
      'donations': '/c/' + slug + '/donations.html',
      'rules': '/c/' + slug + '/rules.html',
      'communities': '/communities',
      'community-admin': '/c/' + slug + '/community-admin.html',
      'admin': '/admin',
      'login': '/login',
      'register': '/register'
    } : {
      'home': 'index.html',
      'cad': 'police.html',
      'police': 'police.html',
      'dmv': 'dmv.html',
      'dispatch': '/join',
      'businesses': 'businesses.html',
      'applications': 'applications.html',
      'complaints': 'complaints.html',
      'donations': 'donations.html',
      'rules': 'rules.html',
      'communities': '/communities',
      'community-admin': '/community-admin',
      'admin': '/admin',
      'login': '/login',
      'register': '/register'
    };
    return pageMap[page] || null;
  }


  function allowedModules() {
    var modules = (window.GTAVCAD_MOBILE_CONTEXT && window.GTAVCAD_MOBILE_CONTEXT.allowed_modules) || [];
    return Array.isArray(modules) ? modules : [];
  }

  function hasModule(module) {
    var mods = allowedModules();
    return mods.indexOf(module) !== -1;
  }

  function safeMenuLink(icon, label, page, currentPage) {
    var href = buildCommunityHref(page);
    if (!href) return '';
    var isActive = currentPage === page ? ' style="color:#fff;border-color:rgba(255,31,31,0.4);"' : '';
    return '<a class="more-menu-link" href="' + href + '"' + isActive + '><span class="link-icon">' + icon + '</span><span class="link-text">' + label + '</span><span class="link-arrow">›</span></a>';
  }

  function injectMobileNav() {
    if (document.getElementById('mobile-bottom-nav')) return;
    var currentPage = getCurrentPage();
    function tab(id, icon, label, page) {
      var isActive = currentPage === page ? ' active' : '';
      if (id === 'tab-more') return '<button class="mobile-tab' + isActive + '" id="tab-more" aria-label="More navigation options" onclick="window.mobileMoreMenuOpen()"><span class="tab-icon">' + icon + '</span>' + label + '</button>';
      var href = buildCommunityHref(page);
      if (!href) return '';
      return '<a class="mobile-tab' + isActive + '" href="' + href + '" aria-label="' + label + '"><span class="tab-icon">' + icon + '</span>' + label + '</a>';
    }
    var nav = document.createElement('nav');
    nav.id = 'mobile-bottom-nav';
    nav.className = 'mobile-bottom-nav';
    nav.setAttribute('aria-label', 'Mobile bottom navigation');
    var tabs = [tab('tab-home', '🏠', 'Home', 'home')];
    if (hasModule('cad')) tabs.push(tab('tab-cad', '🚓', 'CAD', 'cad'));
    if (hasModule('police')) tabs.push(tab('tab-police', '👮', 'Police', 'police'));
    if (hasModule('dmv') || hasModule('dmv_self')) tabs.push(tab('tab-dmv', '🪪', 'DMV', 'dmv'));
    tabs.push(tab('tab-more', '☰', 'More', 'more'));
    nav.innerHTML = tabs.join('');
    document.body.appendChild(nav);
    injectMoreMenu();
  }

  function injectMoreMenu() {
    if (document.getElementById('mobile-more-menu')) return;
    var currentPage = getCurrentPage();
    var authLinks = window.__MOBILE_AUTH_LINKS__ || '<a class="more-menu-link" href="/login"><span class="link-icon">🔐</span><span class="link-text">Login</span><span class="link-arrow">›</span></a><a class="more-menu-link" href="/register"><span class="link-icon">📝</span><span class="link-text">Register</span><span class="link-arrow">›</span></a>';
    var menu = document.createElement('div');
    menu.id = 'mobile-more-menu';
    menu.className = 'mobile-more-menu';
    menu.innerHTML = '<div class="more-menu-header"><div class="more-menu-title"><img src="/assets/images/gtavcad-logo.png" alt="GTAVCAD logo" />GTAVCAD</div><button class="more-menu-close" onclick="window.mobileMoreMenuClose()" aria-label="Close menu">✕</button></div><div class="more-menu-links">'
      + safeMenuLink('🏠', 'Home', 'home', currentPage) + safeMenuLink('🚔', 'CAD', 'cad', currentPage) + safeMenuLink('👮', 'Police', 'police', currentPage) + safeMenuLink('🪪', 'DMV', 'dmv', currentPage)
      + (hasModule('dispatch') ? safeMenuLink('🚨', 'Dispatch', 'dispatch', currentPage) : '') + (hasModule('businesses') ? safeMenuLink('🏢', 'Businesses', 'businesses', currentPage) : '') + (hasModule('applications') ? safeMenuLink('📋', 'Applications', 'applications', currentPage) : '')
      + (hasModule('complaints') ? safeMenuLink('💬', 'Complaints', 'complaints', currentPage) : '') + (hasModule('donations') ? safeMenuLink('💰', 'Donations', 'donations', currentPage) : '') + safeMenuLink('🗺️', 'Maps / Rules', 'rules', currentPage)
      + safeMenuLink('🌐', 'Communities', 'communities', currentPage) + (hasModule('community_admin') ? safeMenuLink('🛡️', 'Admin Tools', 'community-admin', currentPage) : '') + (hasModule('platform_admin') ? safeMenuLink('👑', 'Platform Admin', 'admin', currentPage) : '')
      + '<a class="more-menu-link" href="https://discord.gg/" target="_blank" rel="noopener noreferrer"><span class="link-icon">💬</span><span class="link-text">Join Discord</span><span class="link-arrow">↗</span></a>'
      + authLinks + '</div>';
    document.body.appendChild(menu);
  }


  function refreshMobileNavigation() {
    window.mobileMoreMenuClose();
    var existingNav = document.getElementById('mobile-bottom-nav');
    if (existingNav) existingNav.remove();
    var existingMenu = document.getElementById('mobile-more-menu');
    if (existingMenu) existingMenu.remove();
    injectMobileNav();
    var topTitle = document.querySelector('.mobile-shell-title');
    if (topTitle) {
      var slug = (window.GTAVCAD_CONTEXT && window.GTAVCAD_CONTEXT.communitySlug) || '';
      topTitle.textContent = 'GTAVCAD · ' + (slug || 'No active community');
    }
  }

  window.mobileMoreMenuOpen = function() { var m=document.getElementById('mobile-more-menu'); if (m) { m.classList.add('open'); document.body.style.overflow='hidden'; } };
  window.mobileMoreMenuClose = function() { var m=document.getElementById('mobile-more-menu'); if (m) { m.classList.remove('open'); document.body.style.overflow=''; } };

  async function injectTopMobileShell() {
    document.body.classList.add('mobile-only-app');
    var topbar = document.querySelector('.site-header .topbar');
    if (!topbar) return;
    var user='Guest', role='Logged out', community='No active community';
    try {
      var r=await fetch('/api/auth/session',{credentials:'include'});
      var d=await r.json();
      if (d && d.authenticated && d.user) {
        user=d.user.username||'User';
        role=d.user.role||d.user.community_role||'Member';
        window.GTAVCAD_AUTH_CONTEXT = d.user;
        var unit = d.user.unit || '';
        if (!unit && window.location.pathname.indexOf('/cad') !== -1) { window.showToast && window.showToast('Unable to start officer session: missing unit.', 'warning'); }
      }
    } catch(e) {
      if (window.location.pathname.indexOf('/cad') !== -1) { window.showToast && window.showToast('Unable to start officer session: backend auth/session error.', 'error'); }
    }
    var slug=(window.GTAVCAD_CONTEXT&&window.GTAVCAD_CONTEXT.communitySlug)||'';
    if (slug) community = slug;
    window.__MOBILE_AUTH_LINKS__ = user==='Guest'
      ? '<a class="more-menu-link" href="/login"><span class="link-icon">🔐</span><span class="link-text">Login</span><span class="link-arrow">›</span></a><a class="more-menu-link" href="/register"><span class="link-icon">📝</span><span class="link-text">Register</span><span class="link-arrow">›</span></a>'
      : '<button class="more-menu-link" type="button" data-auth-logout><span class="link-icon">🚪</span><span class="link-text">Logout</span><span class="link-arrow">›</span></button>';
    topbar.innerHTML = '<img class="mobile-shell-logo" src="/assets/images/gtavcad-logo.png" alt="logo"><div class="mobile-shell-meta"><div class="mobile-shell-title">GTAVCAD · '+community+'</div><div class="mobile-shell-sub">'+user+' · '+role+'</div></div><div class="mobile-shell-bell-wrap" id="mobile-shell-bell"></div>';
  }

  function tableToCards() {
    document.querySelectorAll('table').forEach(function(t){
      if (t.dataset.mobileCardsReady === '1') return;
      t.dataset.mobileCardsReady = '1';
      t.classList.add('mobile-table-cards');
      var hiddenHeads = ['actions', 'action', '#', 'id'];
      var heads=[].slice.call(t.querySelectorAll('thead th')).map(function(th){
        return (th.textContent||'').trim();
      });
      t.querySelectorAll('tbody tr').forEach(function(tr){
        var cells = [].slice.call(tr.children);
        var titleCell = cells.find(function(td, i){
          var h = (heads[i] || '').trim().toLowerCase();
          var value = (td.textContent || '').trim();
          return value && hiddenHeads.indexOf(h) === -1;
        });
        tr.setAttribute('data-mobile-card-title', (titleCell && titleCell.textContent || 'Record').trim());
        cells.forEach(function(td,i){
          var label = (heads[i] || ('Field ' + (i+1))).trim();
          if (!label || /^_+$/.test(label)) label = 'Field ' + (i + 1);
          td.setAttribute('data-label', label);
          if (/^actions?$/i.test(label)) td.classList.add('mobile-card-actions');
        });
      });
    });
  }

  async function init() {
    try {
      var contextResp = await fetch('/api/mobile/context', { credentials: 'include' });
      var contextData = await contextResp.json();
      if (contextData && contextData.success) window.GTAVCAD_MOBILE_CONTEXT = contextData;
    } catch (e) {}
    await injectTopMobileShell();
    injectMobileNav();
    tableToCards();
    if (!window.__GTAVCAD_MOBILE_CONTEXT_READY_BOUND__) {
      window.addEventListener('gtavcad:context-ready', function(){ refreshMobileNavigation(); tableToCards(); });
      window.__GTAVCAD_MOBILE_CONTEXT_READY_BOUND__ = true;
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
