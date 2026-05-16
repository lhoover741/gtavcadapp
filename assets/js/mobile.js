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
      'login': 'login', 'register': 'register'
    };
    return map[leaf] || 'home';
  }

  function buildCommunityHref(page) {
    var slug = (window.GTAVCAD_CONTEXT && window.GTAVCAD_CONTEXT.communitySlug) || '';
    var pageMap = slug ? {
      'home': '/c/' + slug + '/', 'cad': '/c/' + slug + '/cad', 'police': '/c/' + slug + '/police.html',
      'dmv': '/c/' + slug + '/dmv.html', 'businesses': '/c/' + slug + '/businesses.html',
      'applications': '/c/' + slug + '/applications.html', 'complaints': '/c/' + slug + '/complaints.html',
      'donations': '/c/' + slug + '/donations.html', 'rules': '/c/' + slug + '/rules.html',
      'community-admin': '/c/' + slug + '/community-admin.html', 'login': '/login', 'register': '/register'
    } : {
      'home': 'index.html', 'cad': 'police.html', 'police': 'police.html', 'dmv': 'dmv.html',
      'businesses': 'businesses.html', 'applications': 'applications.html', 'complaints': 'complaints.html',
      'donations': 'donations.html', 'rules': 'rules.html', 'community-admin': 'community-admin.html',
      'login': '/login', 'register': '/register'
    };
    return pageMap[page] || (slug ? '/c/' + slug + '/' : '/') ;
  }

  function injectMobileNav() {
    if (document.getElementById('mobile-bottom-nav')) return;
    var currentPage = getCurrentPage();
    function tab(id, icon, label, page) {
      var isActive = currentPage === page ? ' active' : '';
      if (id === 'tab-more') return '<button class="mobile-tab' + isActive + '" id="tab-more" aria-label="More navigation options" onclick="window.mobileMoreMenuOpen()"><span class="tab-icon">' + icon + '</span>' + label + '</button>';
      return '<a class="mobile-tab' + isActive + '" href="' + buildCommunityHref(page) + '" aria-label="' + label + '"><span class="tab-icon">' + icon + '</span>' + label + '</a>';
    }
    var nav = document.createElement('nav');
    nav.id = 'mobile-bottom-nav';
    nav.className = 'mobile-bottom-nav';
    nav.setAttribute('aria-label', 'Mobile bottom navigation');
    nav.innerHTML = tab('tab-home', '🏠', 'Home', 'home') + tab('tab-cad', '🚓', 'CAD', 'cad') + tab('tab-police', '👮', 'Police', 'police') + tab('tab-dmv', '🪪', 'DMV', 'dmv') + tab('tab-more', '☰', 'More', 'more');
    document.body.appendChild(nav);
    injectMoreMenu();
  }

  function injectMoreMenu() {
    if (document.getElementById('mobile-more-menu')) return;
    var currentPage = getCurrentPage();
    function menuLink(icon, label, page) {
      var isActive = currentPage === page ? ' style="color:#fff;border-color:rgba(255,31,31,0.4);"' : '';
      return '<a class="more-menu-link" href="' + buildCommunityHref(page) + '"' + isActive + '><span class="link-icon">' + icon + '</span><span class="link-text">' + label + '</span><span class="link-arrow">›</span></a>';
    }
    var authLinks = window.__MOBILE_AUTH_LINKS__ || '<a class="more-menu-link" href="/login"><span class="link-icon">🔐</span><span class="link-text">Login</span><span class="link-arrow">›</span></a><a class="more-menu-link" href="/register"><span class="link-icon">📝</span><span class="link-text">Register</span><span class="link-arrow">›</span></a>';
    var menu = document.createElement('div');
    menu.id = 'mobile-more-menu';
    menu.className = 'mobile-more-menu';
    menu.innerHTML = '<div class="more-menu-header"><div class="more-menu-title"><img src="/assets/images/gtavcad-logo.png" alt="GTAVCAD logo" />GTAVCAD</div><button class="more-menu-close" onclick="window.mobileMoreMenuClose()" aria-label="Close menu">✕</button></div><div class="more-menu-links">'
      + menuLink('🏠', 'Home', 'home') + menuLink('🚔', 'CAD', 'cad') + menuLink('👮', 'Police', 'police') + menuLink('🪪', 'DMV', 'dmv')
      + menuLink('🏢', 'Businesses', 'businesses') + menuLink('📋', 'Applications', 'applications') + menuLink('💬', 'Complaints', 'complaints')
      + menuLink('💰', 'Donations', 'donations') + menuLink('📖', 'Rules', 'rules')
      + '<a class="more-menu-link" href="https://discord.gg/" target="_blank" rel="noopener noreferrer"><span class="link-icon">💬</span><span class="link-text">Join Discord</span><span class="link-arrow">↗</span></a>'
      + authLinks + '</div>';
    document.body.appendChild(menu);
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
    document.querySelectorAll('table').forEach(function(t){ t.classList.add('mobile-table-cards');
      var heads=[].slice.call(t.querySelectorAll('thead th')).map(function(th){return (th.textContent||'').trim();});
      t.querySelectorAll('tbody tr').forEach(function(tr){ [].slice.call(tr.children).forEach(function(td,i){ if(!td.getAttribute('data-label')) td.setAttribute('data-label', heads[i]||('Field '+(i+1)));});});
    });
  }

  async function init() {
    await injectTopMobileShell();
    injectMobileNav();
    tableToCards();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
