# SEARCH-22.5

To setup:

Open a powershell at the root directory and run:
`./setup.bat`

For mac:
`uv venv -p /opt/homebrew/bin/python3.13 .venv`
`uv pip install -r requirements.txt`

-----

To build the database, first build a topology database by setting the desired N and symmetry in `database/tilings/build_topologies.py`. Then, from the root directory run `python -m database.tilings.build_topologies`. Similarly, set the desired N and symmetry in `database/tilings/build_tilings.py` and run `python -m database.tilings.build_tilings`.

To prepare the databases for querying, run `python -m database.tilings.faiss_cache`.

To start the localhost, run `python -m interface.server`.

-----

For more details, refer to `report/report.pdf`.
