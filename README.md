# SEARCH-22.5

## The application can be accessed at: https://225.designorigami.net/

-----

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

More details about this project will be availale soon. Please contact me or join the ExplOri discord server: https://discord.gg/5YcGh8b9yC