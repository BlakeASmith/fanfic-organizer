local DataStorage = require("datastorage")
local Device = require("device")
local logger = require("logger")
local rapidjson = require("rapidjson")
local util = require("util")
local lfs = require("libs/libkoreader-lfs")

local JSON_NAME = "fanfic.collections.json"
local KOBO_STORAGE_ROOTS = {
    main = "/mnt/onboard",
    carda = "/mnt/sd",
    cardb = "/mnt/sd",
}
local SKIP_SCAN_DIRS = {
    cache = true,
    plugins = true,
    ota = true,
    screenshots = true,
    history = true,
    docsettings = true,
    hashdocsettings = true,
    settings = true,
    clipboard = true,
    styletweaks = true,
}

local Metadata = {
    last_error = nil,
}
local library_roots

local function has_metadata_file(dir)
    if util.fileExists(dir .. "/metadata.calibre") then
        return true
    end
    if util.fileExists(dir .. "/.metadata.calibre") then
        return true
    end
    if util.fileExists(dir .. "/.calibre.metadata") then
        return true
    end
    return false
end

local function add_library_root(roots, seen, path)
    if not path or path == "" then
        return
    end
    path = path:gsub("/+$", "")
    if seen[path] then
        return
    end
    if has_metadata_file(path) then
        seen[path] = true
        table.insert(roots, path)
    end
end

local function detect_library_root()
    local root

    local ok_settings, G = pcall(function()
        return G_reader_settings
    end)
    if ok_settings and G and type(G.readSetting) == "function" then
        local home = G:readSetting("home_dir")
        if type(home) == "string" and home ~= "" then
            root = home
        end
    end

    if not root or root == "" then
        if type(Device.home_dir) == "string" and Device.home_dir ~= "" then
            root = Device.home_dir
        end
    end

    if not root or root == "" then
        local ok_fm, filemanagerutil = pcall(require, "apps/filemanager/filemanagerutil")
        if ok_fm and filemanagerutil and type(filemanagerutil.getDefaultDir) == "function" then
            local ok_def, def = pcall(filemanagerutil.getDefaultDir)
            if ok_def and type(def) == "string" and def ~= "" then
                root = def
            end
        end
    end

    if not root or root == "" then
        local ok_root, res = pcall(function()
            if DataStorage.getFullDataDir then
                return DataStorage:getFullDataDir() or DataStorage:getDataDir()
            end
            return DataStorage:getDataDir()
        end)
        if ok_root and type(res) == "string" and res ~= "" then
            root = res
        end
    end

    return root
end

local function find_calibre_metadata(root_dir)
    if not root_dir or root_dir == "" then
        return nil, nil
    end
    local max_depth = 4
    local queue = { { path = root_dir, depth = 0 } }
    local head = 1
    while head <= #queue do
        local item = queue[head]
        head = head + 1
        local dir = item.path
        if has_metadata_file(dir) then
            return dir, dir
        end
        if item.depth < max_depth then
            local ok_iter, iter, dir_obj, err = pcall(lfs.dir, dir)
            if ok_iter and iter then
                for entry in iter, dir_obj do
                    if entry ~= "." and entry ~= ".." and not SKIP_SCAN_DIRS[entry] then
                        local child = dir .. "/" .. entry
                        local mode_ok, mode = pcall(lfs.attributes, child, "mode")
                        if mode_ok and mode == "directory" then
                            table.insert(queue, { path = child, depth = item.depth + 1 })
                        end
                    end
                end
            else
                logger.dbg("fanficcollections: unable to scan directory", dir, err)
            end
        end
    end
    return nil, nil
end

local function load_calibre_search_libraries()
    local ok, Persist = pcall(require, "persist")
    if not ok then
        return nil
    end
    local ok_cache, cache = pcall(function()
        return Persist:new{
            path = DataStorage:getDataDir() .. "/cache/calibre/libraries.lua",
        }
    end)
    if not ok_cache then
        return nil
    end
    local ok_load, data = pcall(function()
        return cache:load()
    end)
    if ok_load then
        return data
    end
    return nil
end

local function collect_scan_starts()
    local starts = {}
    local seen = {}

    local function add_start(path)
        if not path or path == "" then
            return
        end
        path = path:gsub("/+$", "")
        if seen[path] then
            return
        end
        seen[path] = true
        table.insert(starts, path)
    end

    for _, key in ipairs({"SEARCH_LIBRARY_PATH", "SEARCH_LIBRARY_PATH2"}) do
        add_start(G_reader_settings:readSetting(key))
    end

    local cached = load_calibre_search_libraries()
    if type(cached) == "table" then
        for path, enabled in pairs(cached) do
            if enabled then
                add_start(path)
            end
        end
    end

    add_start(detect_library_root())
    if Device:isKobo() or Device:isCervantes() then
        add_start("/mnt")
    end

    return starts
end

function Metadata.library_roots()
    if library_roots then
        return library_roots
    end
    local ok, roots = pcall(function()
        local found = {}
        local seen = {}
        for _, start in ipairs(collect_scan_starts()) do
            add_library_root(found, seen, start)
            local _, lib_root = find_calibre_metadata(start)
            if lib_root then
                add_library_root(found, seen, lib_root)
            end
        end
        return found
    end)
    if ok and type(roots) == "table" then
        library_roots = roots
        return library_roots
    end
    logger.warn("fanficcollections: library root scan failed:", roots)
    library_roots = {}
    return library_roots
end

function Metadata.json_path()
    return DataStorage:getDataDir() .. "/cache/" .. JSON_NAME
end

function Metadata.load_books()
    Metadata.last_error = nil
    library_roots = nil
    local path = Metadata.json_path()
    local handle = io.open(path, "r")
    if not handle then
        logger.info("fanficcollections: no collections JSON at", path)
        return {}, "JSON: " .. path
    end
    local payload = handle:read("*a")
    handle:close()
    local ok, data = pcall(rapidjson.decode, payload)
    if not ok or type(data) ~= "table" then
        Metadata.last_error = ok and _("Invalid collections JSON.") or tostring(data)
        logger.warn("fanficcollections: invalid JSON at", path, Metadata.last_error)
        return {}, "JSON: " .. path
    end
    return data, "JSON: " .. path
end

function Metadata.all_collection_names(books)
    local counts = {}
    if type(books) ~= "table" then
        return {}, counts
    end
    for _, book in ipairs(books) do
        local collections = book.collections
        if type(collections) ~= "table" then
            collections = {}
        end
        for _, name in ipairs(collections) do
            if type(name) == "string" and name ~= "" then
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
    local target = string.lower(collection_name or "")
    local matches = {}
    if type(books) ~= "table" then
        return matches
    end
    for _, book in ipairs(books) do
        local collections = book.collections
        if type(collections) ~= "table" then
            collections = {}
        end
        for _, name in ipairs(collections) do
            if type(name) == "string" and string.lower(name) == target then
                table.insert(matches, book)
                break
            end
        end
    end
    return matches
end

local function append_debug(lines, line)
    table.insert(lines, line)
end

function Metadata.resolve_path(book)
    local path, debug_text = Metadata.resolve_path_with_debug(book)
    return path, debug_text
end

function Metadata.resolve_path_with_debug(book)
    local debug_lines = {}
    if type(book) ~= "table" then
        append_debug(debug_lines, "book entry is not a table")
        return nil, table.concat(debug_lines, "\n")
    end
    local lpath = book.lpath
    append_debug(debug_lines, "lpath: " .. tostring(lpath))
    if type(lpath) ~= "string" or lpath == "" then
        return nil, table.concat(debug_lines, "\n")
    end
    local rootpath = book.rootpath
    if type(rootpath) == "string" and rootpath ~= "" then
        local candidate = rootpath:gsub("/+$", "") .. "/" .. lpath
        append_debug(debug_lines, "try rootpath: " .. candidate)
        if util.fileExists(candidate) then
            return candidate, table.concat(debug_lines, "\n")
        end
    end
    local storage = book.storage
    if type(storage) == "string" then
        append_debug(debug_lines, "storage: " .. storage)
    end
    if type(storage) == "string" and (Device:isKobo() or Device:isCervantes()) then
        local hinted = KOBO_STORAGE_ROOTS[storage]
        if hinted then
            local candidate = hinted .. "/" .. lpath
            append_debug(debug_lines, "try storage root: " .. candidate)
            if util.fileExists(candidate) then
                return candidate, table.concat(debug_lines, "\n")
            end
        end
    end
    local roots = Metadata.library_roots()
    if #roots == 0 then
        append_debug(debug_lines, "no Calibre library roots found (.metadata.calibre)")
        append_debug(debug_lines, "home_dir: " .. tostring(detect_library_root()))
    end
    for _, root in ipairs(roots) do
        local candidate = root .. "/" .. lpath
        append_debug(debug_lines, "try library root: " .. candidate)
        if util.fileExists(candidate) then
            return candidate, table.concat(debug_lines, "\n")
        end
    end
    append_debug(debug_lines, "JSON: " .. Metadata.json_path())
    return nil, table.concat(debug_lines, "\n")
end

return Metadata
