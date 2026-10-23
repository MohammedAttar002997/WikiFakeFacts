"""Show the newest unexpected errors the hosted (Vercel / Neon) site has saved, from your own machine.

    vercel env pull .env.production.local --environment=production
    python production_errors.py            # the newest 20
    python production_errors.py -n 50 --full

Vercel's own logs only last minutes on the Hobby plan, so the app keeps its newest errors in the error_log table.
Connects the same way as migrate_production.py.
"""
import argparse

from migrate_production import use_production_database


def main():
    parser = argparse.ArgumentParser(description='Show the newest errors saved by the hosted site.')
    parser.add_argument('-n', '--limit', type=int, default=20, help='how many to show (default: 20)')
    parser.add_argument('--full', action='store_true', help='also show where in the code each one happened')
    parser.add_argument('--env-file', default='.env.production.local', help='settings file with DATABASE_URL')
    args = parser.parse_args()
    use_production_database(args.env_file)

    # Imported only now: the app reads DATABASE_URL when it's imported
    from app import app, print_errors

    with app.app_context():
        print_errors(args.limit, args.full)


if __name__ == '__main__':
    main()
