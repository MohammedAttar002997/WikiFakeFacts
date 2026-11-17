// The first-visit tour with Finn the detective fox (templates/_tour.html). Steps come from window.WF_TOUR; each one
// highlights the element with data-tour="<target>" (when it's visible) while Finn "says" the text in his bubble.
// Finishing or skipping tells the server (POST data-done-url), so it doesn't start again on this account.
(function () {
    const config = window.WF_TOUR;
    const root = document.getElementById('tour');
    if (!config || !root) return;

    const $ = s => root.querySelector(s);
    const finn = root.querySelector('.finn');
    const spot = $('[data-tour-spot]');
    const next = $('[data-tour-next]');
    const back = $('[data-tour-back]');
    const voiceButton = $('[data-tour-voice]');
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const canSpeak = 'speechSynthesis' in window;
    const csrf = document.querySelector('meta[name="csrf-token"]').content;
    let index = 0, typing = null, voiceOn = false, lastFocus = null;

    try { voiceOn = localStorage.getItem('finnVoice') === 'on'; } catch (e) { /* storage unavailable */ }
    if (!canSpeak) voiceButton.hidden = true;

    function visibleTarget(name) {
        if (!name) return null;
        const el = document.querySelector(`[data-tour="${name}"]`);
        if (!el) return null;
        const box = el.getBoundingClientRect();
        return box.width > 0 && box.height > 0 && getComputedStyle(el).visibility !== 'hidden' ? el : null;
    }

    function placeSpot(el) {
        spot.hidden = !el;
        root.classList.toggle('is-centered', !el);
        if (!el) return;
        const box = el.getBoundingClientRect();
        const pad = 6;
        Object.assign(spot.style, {
            top: (box.top - pad) + 'px', left: (box.left - pad) + 'px',
            width: (box.width + pad * 2) + 'px', height: (box.height + pad * 2) + 'px',
        });
    }

    function speak(text) {
        if (!canSpeak) return;
        window.speechSynthesis.cancel();
        if (!voiceOn) return;
        const line = new SpeechSynthesisUtterance(text);
        line.lang = document.documentElement.lang || 'en';
        line.rate = 1.02;
        line.pitch = 1.15;
        window.speechSynthesis.speak(line);
    }

    function say(text) {
        // Typed out letter by letter while Finn's mouth moves; screen readers get the whole line at once
        clearInterval(typing);
        const typed = $('[data-tour-typed]');
        $('[data-tour-said]').textContent = text;
        speak(text);
        if (reduceMotion) { typed.textContent = text; return; }
        typed.textContent = '';
        finn.classList.add('is-talking');
        let shown = 0;
        typing = setInterval(() => {
            shown += 2;
            typed.textContent = text.slice(0, shown);
            if (shown >= text.length) { clearInterval(typing); finn.classList.remove('is-talking'); }
        }, 28);
    }

    function show(requested) {
        const i = Math.max(0, Math.min(requested, config.steps.length - 1));
        index = i;
        const step = config.steps[index];
        const el = visibleTarget(step.target);
        if (el) {
            // On phones Finn's bubble covers the bottom of the screen, so the target goes near the top instead
            const phone = window.matchMedia('(max-width: 899px)').matches;
            const box = el.getBoundingClientRect();
            const top = Math.max(0, phone ? box.top + window.scrollY - 90
                                          : box.top + window.scrollY - (window.innerHeight - box.height) / 2);
            window.scrollTo({ top, behavior: reduceMotion ? 'auto' : 'smooth' });
            // Smooth scrolling needs animation frames, which a tab in the background doesn't get: if the page
            // hasn't arrived by now, jump there
            setTimeout(() => {
                if (index === i && Math.abs(window.scrollY - top) > 40) window.scrollTo({ top, behavior: 'auto' });
                placeSpot(visibleTarget(step.target));
            }, 600);
        }
        // Wait for the scroll to settle before measuring where the highlight goes
        setTimeout(() => placeSpot(visibleTarget(step.target)), reduceMotion ? 0 : 350);
        $('[data-tour-count]').textContent = config.count.replace('{n}', index + 1).replace('{total}', config.steps.length);
        $('[data-tour-title]').textContent = step.title;
        say(step.text);
        const last = index === config.steps.length - 1;
        next.textContent = last ? next.dataset.doneLabel : next.dataset.nextLabel;
        back.hidden = index === 0;
        const dots = $('[data-tour-dots]');
        dots.innerHTML = config.steps.map((_, n) => `<span class="${n === index ? 'is-on' : ''}"></span>`).join('');
        next.focus();
    }

    function finish() {
        clearInterval(typing);
        if (canSpeak) window.speechSynthesis.cancel();
        root.hidden = true;
        document.documentElement.classList.remove('tour-open');
        fetch(root.dataset.doneUrl, { method: 'POST', headers: { 'X-CSRFToken': csrf } }).catch(() => {});
        if (lastFocus) lastFocus.focus();
    }

    next.addEventListener('click', () => (index === config.steps.length - 1 ? finish() : show(index + 1)));
    back.addEventListener('click', () => show(index - 1));
    root.querySelectorAll('[data-tour-skip]').forEach(b => b.addEventListener('click', finish));
    voiceButton.addEventListener('click', () => {
        voiceOn = !voiceOn;
        try { localStorage.setItem('finnVoice', voiceOn ? 'on' : 'off'); } catch (e) { /* storage unavailable */ }
        drawVoice();
        speak(config.steps[index].text);
    });
    function drawVoice() {
        voiceButton.setAttribute('aria-pressed', String(voiceOn));
        voiceButton.querySelector('i').className = 'bi ' + (voiceOn ? 'bi-volume-up-fill' : 'bi-volume-mute-fill');
    }

    root.addEventListener('keydown', e => {
        if (e.key === 'Escape') { e.preventDefault(); finish(); }
        if (e.key === 'ArrowRight' && e.target.tagName !== 'BUTTON') show(index + 1);
        if (e.key === 'ArrowLeft' && e.target.tagName !== 'BUTTON') show(index - 1);
        if (e.key === 'Tab') {  // Keep focus inside the bubble while the tour is open
            const focusable = [...root.querySelectorAll('button:not([hidden])')].filter(b => b.offsetParent);
            const first = focusable[0], lastOne = focusable[focusable.length - 1];
            if (e.shiftKey && document.activeElement === first) { e.preventDefault(); lastOne.focus(); }
            else if (!e.shiftKey && document.activeElement === lastOne) { e.preventDefault(); first.focus(); }
        }
    });
    window.addEventListener('resize', () => placeSpot(visibleTarget(config.steps[index].target)));
    window.addEventListener('scroll', () => placeSpot(visibleTarget(config.steps[index].target)), { passive: true });

    function start() {
        lastFocus = document.activeElement;
        root.hidden = false;
        document.documentElement.classList.add('tour-open');
        drawVoice();
        show(0);
    }
    if (document.readyState === 'complete') start(); else window.addEventListener('load', start);
})();
