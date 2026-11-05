// Keeps floating things on screen. One shared tooltip for every
// [data-tooltip] element lives on <body>
// with position: fixed, so scrolling tables can't clip it, and it's placed
// by measuring the screen: above the element, flipping below when there's
// no room, and pushed back in from any edge.
(function () {
    if (window.__appTooltips) return;
    window.__appTooltips = true;

    var EDGE = 8;
    var GAP = 8;
    var tip = null;
    var current = null;

    function element() {
        if (!tip || !tip.isConnected) {
            tip = document.createElement('div');
            tip.id = 'app-tooltip';
            tip.setAttribute('role', 'tooltip');
            document.body.appendChild(tip);
        }
        return tip;
    }

    function place(target) {
        var box = target.getBoundingClientRect();
        var width = tip.offsetWidth;
        var height = tip.offsetHeight;
        var maxTop = window.innerHeight - height - EDGE;
        var maxLeft = document.documentElement.clientWidth - width - EDGE;

        var top = box.top - height - GAP;
        if (top < EDGE) {
            var below = box.bottom + GAP;
            // Stay above if neither side fits and above has more room.
            if (below <= maxTop || window.innerHeight - box.bottom >= box.top) top = below;
        }
        var left = box.left + box.width / 2 - width / 2;
        tip.style.top = Math.max(EDGE, Math.min(top, maxTop)) + 'px';
        tip.style.left = Math.max(EDGE, Math.min(left, maxLeft)) + 'px';
    }

    function show(target) {
        var text = target.getAttribute('data-tooltip');
        if (!text) return hide();
        current = target;
        element();
        tip.textContent = text;
        tip.classList.toggle('is-cell', !!target.closest('table.month td'));
        tip.classList.toggle('is-warning', target.classList.contains('salary-tab-warning'));
        tip.classList.add('is-visible');
        place(target);
    }

    function hide() {
        current = null;
        if (tip) tip.classList.remove('is-visible');
    }

    function targetOf(node) {
        return node && node.closest ? node.closest('[data-tooltip]') : null;
    }

    document.addEventListener('mouseover', function (event) {
        var target = targetOf(event.target);
        if (target && target !== current) show(target);
        else if (!target && current) hide();
    });
    document.addEventListener('mouseout', function (event) {
        if (current && !(event.relatedTarget && current.contains(event.relatedTarget))) hide();
    });
    document.addEventListener('focusin', function (event) {
        var target = targetOf(event.target);
        if (target && event.target.matches(':focus-visible')) show(target);
    });
    document.addEventListener('focusout', hide);
    document.addEventListener('click', hide, true);
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') hide();
    });
    window.addEventListener('scroll', hide, true);
    window.addEventListener('resize', hide);
    document.addEventListener('htmx:beforeSwap', hide);

    // Locking a month takes several seconds while it saves snapshots. Show
    // that it's working and ignore repeat clicks, which would run the same
    // save twice at once and fail with "database is locked".
    document.addEventListener('submit', function (event) {
        var form = event.target;
        if (!form.matches || !form.matches('form[action*="toggle-month-lock"]')) return;
        if (form.dataset.submitting) {
            event.preventDefault();
            return;
        }
        form.dataset.submitting = 'true';
        var button = event.submitter || form.querySelector('[type="submit"]');
        if (button) button.textContent = 'Please wait...';
    });
    window.addEventListener('pageshow', function () {
        document.querySelectorAll('form[data-submitting]').forEach(function (form) {
            delete form.dataset.submitting;
        });
    });

    // A dropdown taller than the room below it scrolls, so nothing at its
    // bottom (like a Send button) ends up off screen.
    document.addEventListener('shown.bs.dropdown', function (event) {
        var menu = event.target.parentElement && event.target.parentElement.querySelector('.dropdown-menu.show');
        if (!menu) return;
        // Bootstrap positions the menu just after this event fires.
        setTimeout(function () {
            var room = window.innerHeight - menu.getBoundingClientRect().top - EDGE;
            if (menu.offsetHeight > room) {
                menu.style.maxHeight = Math.max(room, 120) + 'px';
                menu.style.overflowY = 'auto';
            }
        }, 0);
    });
    document.addEventListener('hidden.bs.dropdown', function (event) {
        var menu = event.target.parentElement && event.target.parentElement.querySelector('.dropdown-menu');
        if (menu) {
            menu.style.maxHeight = '';
            menu.style.overflowY = '';
        }
    });
})();
