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

Trong hàm __main__, đề xuất các giá trị default là N = 3, symmetry = "none", và prefix_length = 4. Như này, database size khởi tạo khoảng 3 MB, và mất khoảng 1 hour. Với các giá trị lớn hơn, có thể mất 63h(3 ngày) thậm chí hàng tháng, hay hàng năm để khởi tạo!!!

Trước khi khởi tạo, còn phải làm như sau (trên Windows và Vscode):
    1. Cài Visual Studio Build Tools (5 phút):
        Chọn workload: "Desktop development with C++".
        Này nghĩa là đã bao gồm MSVC và Windows SDK rồi.
    2. Xong xuôi, trong terminal project root, chạy (5 phút):
        python -m pip install pybind11
        python setup.py build_ext --inplace
    3. Xong xuôi, thành công rồi, hãy build_topologies. Này là để generate ~ 466000 topology raw states(theo setting ghi bên trên), ứng với 68873 Unique Topologies và 2.8Mb data. Nói là mất 1h, thực tế tôi mất khoảng ... 50h, do càng về cuối càng chậm.
        python -m database.tilings.build_topologies
    4. Sau khi complete build_topologies, hãy build_tilings. Này là để đưa các topology raw states vào tiling DB. Mất khoảng 12h, file size khoảng 256Mb.
        python -m database.tilings.build_tilings

To prepare the databases for querying, run `python -m database.tilings.faiss_cache`. Này chỉ mất khoảng 5 phút.

To start the localhost, run `python -m interface.server`.
To change the default port 8000, see SEARCH22_INTERFACE_PORT in server.py, hoặc chỉ định port trực tiếp:
    $env:SEARCH22_INTERFACE_PORT='8081'; python -m interface.server

-----

More details about this project will be availale soon. Please contact me or join the ExplOri discord server: https://discord.gg/5YcGh8b9yC