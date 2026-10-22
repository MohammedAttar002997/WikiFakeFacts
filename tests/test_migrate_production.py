"""migrate_production.py: the pg8000 fallback gets a URL it understands."""
import pytest

from migrate_production import pg8000_url


@pytest.mark.parametrize('url, expected', [
    ('postgresql://u:p@ep-x.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require',
     'postgresql+pg8000://u:p@ep-x.eu-central-1.aws.neon.tech/neondb'),
    ('postgres://u:p@host:5432/db?sslmode=require&application_name=wff',
     'postgresql+pg8000://u:p@host:5432/db?application_name=wff'),
])
def test_pg8000_url_drops_libpq_only_options(url, expected):
    assert pg8000_url(url) == expected
