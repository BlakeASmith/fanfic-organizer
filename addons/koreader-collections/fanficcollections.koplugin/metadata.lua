local DataStorage = require("datastorage")
local Device = require("device")
local logger = require("logger")
local rapidjson = require("rapidjson")
local util = require("util")
local lfs = require("libs/libkoreader-lfs")

local JSON_NAME = "fanfic.collections.json"

local Metadata = {}
local library_roots

local function metadata_file_in(dir)
    if util.fileExists(dir .. "/metadata.calibre") then
        return dir
    end
    if util.fileExists(dir .. "/.metadata.calibre") then
        return dir
    end
end

local function add_library_root(roots, seen, path)
    if not path or path == "" then
        return
    end
    path = path:gsub("/+$", "")
    if seen[path] then
        return
    end
    if metadata_file_in(path) then
        seen[path] = true
        table.insert(roots, path)
    end
end

local function scan_for_libraries(root_dir, roots, seen, depth)
    depth = depth or 0
    if depth > 8 or not root_dir or root_dir == "" then
        return
    end
    add_library_root(roots, seen, root_dir)
    local ok, iter, dir_obj = pcall(lfs.dir, root_dir)
    if not ok then
        return
    end
    for entry in iter, dir_obj do
        if entry ~= "." and entry ~= ".." then
            local path = root_dir .. "/" .. entry
            if lfs.attributes(path, "mode") == "directory" then
                add_library_root(roots, seen, path)
                if depth < 8 then
                    scan_for_libraries(path, roots, seen, depth + 1)
                end
            end
        end
    end
end

function Metadata.library_roots()
    if library_roots then
        return library_roots
    end
    local roots = {}
    local seen = {}
    for _, key in ipairs({"SEARCH_LIBRARY_PATH", "SEARCH_LIBRARY_PATH2"}) do
        add_library_root(roots, seen, G_reader_settings:readSetting(key))
    end
    local scan_root
    if Device:isKobo() or Device:isCervantes() then
        scan_root = "/mnt"
    elseif Device:isAndroid() then
        scan_root = Device.home_dir
    else
        scan_root = Device.home_dir or lfs.currentdir()
    end
    scan_for_libraries(scan_root, roots, seen, 0)
    library_roots = roots
    return library_roots
end

function Metadata.json_path()
    return DataStorage:getDataDir() .. "/cache/" .. JSON_NAME
end

function Metadata.load_books()
    local path = Metadata.json_path()
    local handle = io.open(path, "r")
    if not handle then
        logger.info("fanficcollections: no collections JSON at", path)
        return {}
    end
    local payload = handle:read("*a")
    handle:close()
    local ok, data = pcall(rapidjson.decode, payload)
    if not ok or type(data) ~= "table" then
        logger.warn("fanficcollections: invalid JSON at", path)
        return {}
    end
    return data
end

function Metadata.all_collection_names(books)
    local counts = {}
    for _, book in ipairs(books) do
        local collections = book.collections or {}
        for _, name in ipairs(collections) do
            if name and name ~= "" then
                counts[name] = (counts[name] or 0) + 1
            end
        end
    end
    local names = {}
    for name, _ in pairs(counts) do
        table.insert(names, name)
    end
    table.sort(names, function(a, b)
        return a:lower() < b:lower()
    end)
    return names, counts
end

function Metadata.books_in_collection(books, collection_name)
    local matches = {}
    for _, book in ipairs(books) do
        local collections = book.collections or {}
        for _, name in ipairs(collections) do
            if name == collection_name then
                table.insert(matches, book)
                break
            end
        end
    end
    return matches
end

function Metadata.resolve_path(book)
    if type(book) ~= "table" then
        return nil
    end
    local lpath = book.lpath
    if not lpath or lpath == "" then
        return nil
    end
    local rootpath = book.rootpath
    if rootpath and rootpath ~= "" then
        local path = rootpath:gsub("/+$", "") .. "/" .. lpath
        if util.fileExists(path) then
            return path
        end
    end
    for _, root in ipairs(Metadata.library_roots()) do
        local path = root .. "/" .. lpath
        if util.fileExists(path) then
            return path
        end
    end
    return nil
end

return Metadata
