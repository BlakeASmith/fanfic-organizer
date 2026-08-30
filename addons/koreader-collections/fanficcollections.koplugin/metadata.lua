local DataStorage = require("datastorage")
local Device = require("device")
local logger = require("logger")
local rapidjson = require("rapidjson")
local util = require("util")

local JSON_NAME = "fanfic.collections.json"
local KOBO_STORAGE_ROOTS = {
    main = "/mnt/onboard",
    carda = "/mnt/sd",
    cardb = "/mnt/sd",
}

local Metadata = {
    last_error = nil,
}
local book_roots

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

function Metadata.library_roots()
    if book_roots then
        return book_roots
    end
    local ok, roots = pcall(function()
        local found = {}
        local seen = {}

        local function add(path)
            if not path or path == "" then
                return
            end
            path = path:gsub("/+$", "")
            if seen[path] then
                return
            end
            seen[path] = true
            table.insert(found, path)
        end

        add(detect_library_root())
        if Device:isKobo() or Device:isCervantes() then
            add("/mnt/onboard")
            add("/mnt/sd")
        end
        return found
    end)
    if ok and type(roots) == "table" then
        book_roots = roots
        return book_roots
    end
    logger.warn("fanficcollections: book root scan failed:", roots)
    book_roots = {}
    return book_roots
end

function Metadata.json_path()
    return DataStorage:getDataDir() .. "/cache/" .. JSON_NAME
end

function Metadata.load_books()
    Metadata.last_error = nil
    book_roots = nil
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
        append_debug(debug_lines, "no book search roots found")
        append_debug(debug_lines, "home_dir: " .. tostring(detect_library_root()))
    end
    for _, root in ipairs(roots) do
        local candidate = root .. "/" .. lpath
        append_debug(debug_lines, "try book root: " .. candidate)
        if util.fileExists(candidate) then
            return candidate, table.concat(debug_lines, "\n")
        end
    end
    append_debug(debug_lines, "JSON: " .. Metadata.json_path())
    return nil, table.concat(debug_lines, "\n")
end

return Metadata
