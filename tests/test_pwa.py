"""The site installs as an app (PWA): manifest, icons, a cautious service worker and an offline page."""
import json
import os

from PIL import Image

STATIC = os.path.join(os.path.dirname(__file__), '..', 'public', 'static')


def static_path(url):
    return os.path.join(STATIC, url.removeprefix('/static/'))


def test_manifest_has_what_browsers_need_to_install():
    with open(os.path.join(STATIC, 'manifest.webmanifest'), encoding='utf-8') as f:
        manifest = json.load(f)
    assert manifest['name'] and manifest['short_name'] and manifest['display'] == 'standalone'
    assert manifest['start_url'].startswith('/') and manifest['scope'] == '/'
    sizes = {(i['sizes'], i.get('purpose', 'any')) for i in manifest['icons']}
    assert {('192x192', 'any'), ('512x512', 'any'), ('512x512', 'maskable')} <= sizes


def test_every_icon_exists_in_its_stated_size():
    with open(os.path.join(STATIC, 'manifest.webmanifest'), encoding='utf-8') as f:
        manifest = json.load(f)
    icons = manifest['icons'] + [{'src': '/static/img/icons/apple-touch-icon.png', 'sizes': '180x180'}]
    for icon in icons:
        with Image.open(static_path(icon['src'])) as image:
            assert '%dx%d' % image.size == icon['sizes'], icon['src']


def test_every_page_links_the_manifest_and_icons(app):
    html = app.test_client().get('/').get_data(as_text=True)
    assert '<link rel="manifest" href="/static/manifest.webmanifest">' in html
    assert '<meta name="theme-color" content="#7c3aed">' in html
    assert 'apple-touch-icon.png' in html


def test_service_worker_is_served_from_the_root_and_never_cached(app):
    response = app.test_client().get('/sw.js')
    assert response.status_code == 200 and response.mimetype == 'application/javascript'
    assert response.headers['Service-Worker-Allowed'] == '/' and response.headers['Cache-Control'] == 'no-cache'
    assert b"addEventListener('fetch'" in response.data
    response.close()


def test_service_worker_only_keeps_static_files_and_the_offline_page():
    """Structure, not behaviour (there's no browser here): it must leave non-GET requests, other sites and anything
    outside /static/ to the network, and fall back to the offline page only for page loads."""
    with open(os.path.join(STATIC, 'js', 'sw.js'), encoding='utf-8') as f:
        sw = f.read()
    assert "request.method !== 'GET'" in sw and 'url.origin !== self.location.origin' in sw
    assert "url.pathname.startsWith('/static/')" in sw
    assert "request.mode === 'navigate'" in sw and 'caches.match(OFFLINE_URL)' in sw
    assert sw.count('cache.put(') == 1  # only in the /static/ branch


def test_offline_page_works_and_says_nothing_about_the_player(login):
    html = login('Phoenix').get('/offline').get_data(as_text=True)
    assert "<h1>You're offline</h1>" in html and 'Try again' in html
    assert 'Phoenix' not in html  # it's saved once and shown later, so nothing that can go stale


def test_app_js_registers_the_worker_and_offers_installing():
    with open(os.path.join(STATIC, 'js', 'app.js'), encoding='utf-8') as f:
        js = f.read()
    assert "navigator.serviceWorker.register('/sw.js')" in js and 'beforeinstallprompt' in js


def test_install_button_starts_hidden(app):
    html = app.test_client().get('/').get_data(as_text=True)
    assert '<span id="installApp" hidden>' in html and 'Install app' in html
