local DataStorage = require("datastorage")
local logger = require("logger")
local rapidjson = require("rapidjson")

local JSON_NAME = "fanfic.collections.json"

local Metadata = {}

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

function Metadata.resolve_path(lpath)
    if not lpath or lpath == "" then
        return nil
    end
    return DataStorage:getDataDir() .. "/" .. lpath
end

return Metadata
