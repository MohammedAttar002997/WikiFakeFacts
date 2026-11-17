// Shared UI behaviour: theme toggle and the "generating quiz" loading overlay.
// The initial theme is applied by an inline script in <head> to avoid a flash.

(function () {
    const root = document.documentElement;
    // Interface strings from base.html; English if a page doesn't provide them
    const i18n = window.WF_I18N || {};

    function storedTheme() {
        try { return localStorage.getItem('theme'); } catch (e) { return null; }
    }

    function applyTheme(theme) {
        root.setAttribute('data-bs-theme', theme);
        document.querySelectorAll('[data-theme-toggle]').forEach(btn => {
            btn.setAttribute('aria-label', theme === 'dark' ? (i18n.switchToLight || 'Switch to light mode') : (i18n.switchToDark || 'Switch to dark mode'));
            btn.setAttribute('title', theme === 'dark' ? (i18n.lightMode || 'Light mode') : (i18n.darkMode || 'Dark mode'));
        });
    }

    applyTheme(root.getAttribute('data-bs-theme') || 'light');

    document.querySelectorAll('[data-theme-toggle]').forEach(btn => {
        btn.addEventListener('click', () => {
            const next = root.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark';
            try { localStorage.setItem('theme', next); } catch (e) { /* storage unavailable */ }
            applyTheme(next);
        });
    });

    // Follow the OS setting until the user picks a theme explicitly
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    media.addEventListener('change', e => {
        if (!storedTheme()) applyTheme(e.matches ? 'dark' : 'light');
    });

    // Loading overlay for forms and links that trigger slow AI generation
    const overlay = document.getElementById('loadingOverlay');
    const tipEl = document.getElementById('loadingTip');
    const tipSets = (i18n.tips) || {
        quiz: [
            'Reading up on your topic…',
            'Mixing three true facts with one sneaky fake…',
            'Making the fake fact extra convincing…',
            'Shuffling the answers…',
            'Almost there — sharpen your detective skills!'
        ],
        analysis: [
            'Gathering your quiz history…',
            'Crunching your scores by topic…',
            'Spotting your strongest areas…',
            'Finding topics worth another try…',
            'Writing your personal learning path…'
        ]
    };
    let tipTimer = null;

    function showLoading(message, tipSet) {
        if (!overlay) return;
        const tips = tipSets[tipSet] || tipSets.quiz;
        overlay.querySelector('[data-loading-title]').textContent = message;
        window.finnMood(overlay.querySelector('.finn'), 'search');
        let i = 0;
        tipEl.textContent = tips[0];
        tipTimer = setInterval(() => {
            i = (i + 1) % tips.length;
            tipEl.style.opacity = 0;
            setTimeout(() => { tipEl.textContent = tips[i]; tipEl.style.opacity = 1; }, 300);
        }, 2600);
        overlay.classList.add('show');
    }

    function hideLoading() {
        if (!overlay) return;
        clearInterval(tipTimer);
        overlay.classList.remove('show');
    }

    document.querySelectorAll('form[data-loading]').forEach(form => {
        form.addEventListener('submit', e => {
            // Defer so page-specific validators that call preventDefault() run first
            setTimeout(() => {
                if (e.defaultPrevented || (e.submitter && e.submitter.hasAttribute('data-no-loading'))) return;
                const btn = form.querySelector('[type="submit"]');
                if (btn) btn.disabled = true;
                showLoading(form.dataset.loading, form.dataset.loadingTips);
            }, 0);
        });
    });

    document.querySelectorAll('a[data-loading]').forEach(link => {
        link.addEventListener('click', e => {
            // Leave new-tab/window clicks alone; only the current page navigates away
            if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
            showLoading(link.dataset.loading, link.dataset.loadingTips);
        });
    });

    // Reset when the page is restored from the back/forward cache
    window.addEventListener('pageshow', e => {
        if (e.persisted) {
            hideLoading();
            document.querySelectorAll('form[data-loading] [type="submit"]').forEach(b => { b.disabled = false; });
        }
    });

    // Installable app: register the service worker, and offer "Install app" when the browser allows it
    if ('serviceWorker' in navigator) {
        window.addEventListener('load', () => {
            navigator.serviceWorker.register('/sw.js').catch(() => { /* the site works without it */ });
        });
    }
    const install = document.getElementById('installApp');
    let installPrompt = null;
    window.addEventListener('beforeinstallprompt', e => {
        e.preventDefault();  // no automatic banner; the footer button asks instead
        installPrompt = e;
        if (install) install.hidden = false;
    });
    if (install) install.querySelector('button').addEventListener('click', async () => {
        if (!installPrompt) return;
        installPrompt.prompt();
        await installPrompt.userChoice;
        installPrompt = null;
        install.hidden = true;
    });
    window.addEventListener('appinstalled', () => { if (install) install.hidden = true; });

    // Daily-challenge reminders (daily page): switch push notifications on or off for this device
    const reminderCard = document.querySelector('[data-push]');
    if (reminderCard) setUpReminders(reminderCard).catch(() => { /* the page works without them */ });

    // Finn the fox (_finn.html): his pupils follow the pointer, and pages set his mood with window.finnMood
    const MOODS = ['happy', 'sad', 'wow', 'worried', 'think', 'search'];
    window.finnMood = (finn, mood) => {
        if (!finn) return;
        MOODS.forEach(m => finn.classList.toggle('mood-' + m, m === mood));
    };
    if (window.matchMedia('(pointer: fine)').matches
            && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        let frame = null;
        document.addEventListener('pointermove', e => {
            if (frame) return;  // At most once per frame
            frame = requestAnimationFrame(() => {
                frame = null;
                // Looked up each time: the tour's Finn is added to the page after this script runs
                document.querySelectorAll('.finn').forEach(finn => {
                    const box = finn.getBoundingClientRect();
                    if (!box.width) return;  // Not on screen (the loading screen's Finn, say)
                    // From his eyes (a third of the way down) to the pointer; the further away, the further he looks
                    const dx = e.clientX - (box.left + box.width * .47), dy = e.clientY - (box.top + box.height * .33);
                    const distance = Math.hypot(dx, dy) || 1, reach = Math.min(1, distance / 160);
                    finn.style.setProperty('--look-x', (dx / distance * 2.4 * reach).toFixed(2) + 'px');
                    finn.style.setProperty('--look-y', (dy / distance * 2 * reach).toFixed(2) + 'px');
                    finn.classList.add('is-watching');
                });
            });
        }, { passive: true });
    }

    // Help button: on phones it covered whatever the player was reading, so it tucks away while they scroll
    // down and comes back when they scroll up or reach the end of the page
    const fab = document.querySelector('.chatbot-fab');
    const phone = window.matchMedia('(max-width: 576px)');
    let lastY = window.scrollY;
    if (fab) {
        window.addEventListener('scroll', () => {
            const y = window.scrollY;
            if (Math.abs(y - lastY) < 8) return;  // ignore jitter
            const atEnd = window.innerHeight + y >= document.documentElement.scrollHeight - 80;
            fab.classList.toggle('is-tucked', phone.matches && y > lastY && y > 80 && !atEnd);
            lastY = y;
        }, { passive: true });
    }

    async function setUpReminders(card) {
        const button = card.querySelector('[data-push-button]');
        const note = card.querySelector('[data-push-note]');
        const text = card.dataset;
        card.hidden = false;
        if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
            button.hidden = true;
            note.textContent = text.unsupported;
            return;
        }
        const registration = await navigator.serviceWorker.register('/sw.js');
        await navigator.serviceWorker.ready;
        let subscription = await registration.pushManager.getSubscription();
        const show = () => {
            button.textContent = subscription ? text.off : text.on;
            button.classList.toggle('btn-primary', !subscription);
            button.classList.toggle('btn-outline-secondary', !!subscription);
            card.classList.toggle('is-on', !!subscription);
        };
        const post = (url, body) => fetch(url, {
            method: 'POST', body: JSON.stringify(body),
            headers: { 'Content-Type': 'application/json',
                       'X-CSRFToken': document.querySelector('meta[name="csrf-token"]').content },
        }).then(response => { if (!response.ok) throw new Error(response.status); });
        show();
        if (Notification.permission === 'denied') note.textContent = text.blocked;

        button.addEventListener('click', async () => {
            button.disabled = true;
            try {
                if (subscription) {
                    const endpoint = subscription.endpoint;
                    await subscription.unsubscribe();
                    subscription = null;
                    await post('/push/unsubscribe', { endpoint });
                    note.textContent = text.note;
                } else if (await Notification.requestPermission() !== 'granted') {
                    note.textContent = text.blocked;
                } else {
                    subscription = await registration.pushManager.subscribe(
                        { userVisibleOnly: true, applicationServerKey: keyBytes(text.key) });
                    await post('/push/subscribe', subscription.toJSON());
                    note.textContent = text.done;
                }
            } catch (e) {
                note.textContent = text.error;
            } finally {
                button.disabled = false;
                show();
            }
        });
    }

    function keyBytes(base64url) {
        const padded = base64url + '='.repeat((4 - base64url.length % 4) % 4);
        return Uint8Array.from(atob(padded.replace(/-/g, '+').replace(/_/g, '/')), c => c.charCodeAt(0));
    }
})();
