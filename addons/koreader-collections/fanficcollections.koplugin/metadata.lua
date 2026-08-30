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
local ANDROID_PRIMARY_ROOTS = {
    "/storage/emulated/0",
    "/sdcard",
}

local Metadata = {
    last_error = nil,
}
local cached_book_roots

local function normalize_slash(path)
    if type(path) ~= "string" or path == "" then
        return ""
    end
    path = path:gsub("\\", "/"):gsub("/+", "/")
    return path:gsub("/+$", "")
end

local function detect_home_dir()
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

    return root
end

local function add_unique_root(roots, seen, path)
    path = normalize_slash(path)
    if path == "" or seen[path] then
        return
    end
    seen[path] = true
    table.insert(roots, path)
end

local function android_external_sd_root()
    if not Device.isAndroid then
        return nil
    end
    if type(Device.hasExternalSD) ~= "function" then
        return nil
    end
    local ok, path = pcall(Device.hasExternalSD, Device)
    if ok and type(path) == "string" and path ~= "" then
        return path
    end
    return nil
end

local function storage_roots(storage)
    local roots = {}
    local seen = {}
    local home = detect_home_dir()

    if Device:isKobo() or Device:isCervantes() then
        if not storage or storage == "main" then
            add_unique_root(roots, seen, KOBO_STORAGE_ROOTS.main)
        end
        if not storage or storage == "carda" or storage == "cardb" then
            add_unique_root(roots, seen, KOBO_STORAGE_ROOTS.carda)
        end
    end

    if Device.isAndroid then
        if not storage or storage == "main" then
            add_unique_root(roots, seen, home)
            if type(Device.home_dir) == "string" then
                add_unique_root(roots, seen, Device.home_dir)
            end
            for _, path in ipairs(ANDROID_PRIMARY_ROOTS) do
                add_unique_root(roots, seen, path)
            end
        end
        if not storage or storage == "carda" or storage == "cardb" then
            add_unique_root(roots, seen, android_external_sd_root())
        end
    end

    if not Device:isKobo() and not Device:isCervantes() and not Device.isAndroid then
        add_unique_root(roots, seen, home)
        if type(Device.home_dir) == "string" then
            add_unique_root(roots, seen, Device.home_dir)
        end
    end

    return roots
end

function Metadata.library_roots()
    if cached_book_roots then
        return cached_book_roots
    end
    cached_book_roots = storage_roots(nil)
    return cached_book_roots
end

local function join_candidates(root, lpath)
    root = normalize_slash(root)
    lpath = lpath:gsub("^/+", "")
    if root == "" or lpath == "" then
        return {}
    end
    local candidates = {}
    local seen = {}
    local function add(path)
        path = normalize_slash(path)
        if path ~= "" and not seen[path] then
            seen[path] = true
            table.insert(candidates, path)
        end
    end

    add(root .. "/" .. lpath)
    local root_name = root:match("([^/]+)$")
    if root_name and lpath:lower():find("^" .. root_name:lower():gsub("([%-%.%+%[%]%(%)%$%^%%%?%*])", "%%%1") .. "/") then
        local parent = root:match("^(.*)/[^/]+$")
        if parent and parent ~= "" then
            add(parent .. "/" .. lpath)
        end
    end
    return candidates
end

local function exists_readable(path)
    if util.fileExists(path) then
        return path
    end
    local dir, name = path:match("^(.+)/([^/]+)$")
    if not dir or not name or not util.pathExists(dir) then
        return nil
    end
    local ok, iter, dir_obj = pcall(lfs.dir, dir)
    if not ok or not iter then
        return nil
    end
    local target = name:lower()
    for entry in iter, dir_obj do
        if entry ~= "." and entry ~= ".." and entry:lower() == target then
            local candidate = dir .. "/" .. entry
            if util.fileExists(candidate) then
                return candidate
            end
        end
    end
    return nil
end

local function append_debug(lines, line)
    table.insert(lines, line)
end

local function try_candidates(candidates, debug_lines, label)
    for _, candidate in ipairs(candidates) do
        append_debug(debug_lines, label .. candidate)
        local resolved = exists_readable(candidate)
        if resolved then
            return resolved
        end
    end
    return nil
end

local function path_suffix_match(fullpath, lpath)
    fullpath = normalize_slash(fullpath):lower()
    lpath = normalize_slash(lpath):lower()
    if fullpath == lpath then
        return true
    end
    if fullpath:sub(-#lpath) == lpath then
        local pos = #fullpath - #lpath
        return pos == 0 or fullpath:sub(pos, pos) == "/"
    end
    return false
end

local function search_under_roots(roots, lpath, debug_lines)
    local target = normalize_slash(lpath)
    if target == "" then
        return nil
    end
    local filename = target:match("([^/]+)$") or target
    for _, root in ipairs(roots) do
        if not util.pathExists(root) then
            append_debug(debug_lines, "skip missing root: " .. root)
        else
            local found
            util.findFiles(root, function(fullpath, name)
                if found then
                    return
                end
                if path_suffix_match(fullpath, target) then
                    found = fullpath
                    return
                end
                if name:lower() == filename:lower() and path_suffix_match(fullpath, filename) then
                    found = fullpath
                end
            end, true, 8000)
            if found then
                append_debug(debug_lines, "search found: " .. found)
                return exists_readable(found) or found
            end
        end
    end
    return nil
end

function Metadata.json_path()
    return DataStorage:getDataDir() .. "/cache/" .. JSON_NAME
end

function Metadata.load_books()
    Metadata.last_error = nil
    cached_book_roots = nil
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
    lpath = lpath:gsub("\\", "/")

    local rootpath = book.rootpath
    if type(rootpath) == "string" and rootpath ~= "" then
        local resolved = try_candidates(join_candidates(rootpath, lpath), debug_lines, "try rootpath: ")
        if resolved then
            return resolved, table.concat(debug_lines, "\n")
        end
    end

    local storage = book.storage
    if type(storage) == "string" then
        append_debug(debug_lines, "storage: " .. storage)
    end

    local storage_specific = storage_roots(storage)
    local candidates = {}
    for _, root in ipairs(storage_specific) do
        for _, candidate in ipairs(join_candidates(root, lpath)) do
            table.insert(candidates, candidate)
        end
    end
    local resolved = try_candidates(candidates, debug_lines, "try storage root: ")
    if resolved then
        return resolved, table.concat(debug_lines, "\n")
    end

    local fallback_roots = Metadata.library_roots()
    candidates = {}
    for _, root in ipairs(fallback_roots) do
        for _, candidate in ipairs(join_candidates(root, lpath)) do
            table.insert(candidates, candidate)
        end
    end
    resolved = try_candidates(candidates, debug_lines, "try book root: ")
    if resolved then
        return resolved, table.concat(debug_lines, "\n")
    end

    resolved = search_under_roots(storage_specific, lpath, debug_lines)
    if not resolved then
        resolved = search_under_roots(fallback_roots, lpath, debug_lines)
    end
    if resolved then
        return resolved, table.concat(debug_lines, "\n")
    end

    append_debug(debug_lines, "home_dir: " .. tostring(detect_home_dir()))
    append_debug(debug_lines, "JSON: " .. Metadata.json_path())
    return nil, table.concat(debug_lines, "\n")
end

return Metadata
