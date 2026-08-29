local ConfirmBox = require("ui/widget/confirmbox")
local Device = require("device")
local Menu = require("ui/widget/menu")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetContainer")
local ReaderUI = require("apps/reader/readerui")
local _ = require("gettext")

local Screen = Device.screen
local Metadata = require("metadata")

local FanficCollections = WidgetContainer:new{
    name = "fanficcollections",
}

function FanficCollections:init()
    self:onDispatcherRegisterActions()
end

function FanficCollections:onDispatcherRegisterActions()
    self.ui.menu:registerToMainMenu(self)
end

function FanficCollections:addToMainMenu(menu_items)
    menu_items.fanfic_collections = {
        text = _("Fanfic collections"),
        sorting_hint = "search",
        callback = function()
            self:show_collection_picker()
        end,
    }
end

function FanficCollections:show_collection_picker()
    local books = Metadata.load_books()
    if #books == 0 then
        UIManager:show(ConfirmBox:new{
            text = _(
                "No fanfic-organizer collections found.\n\n"
                    .. "Connect your Kobo to Calibre, enable KOReader support "
                    .. "in Fanfic Organizer settings, and sync once."
            ),
        })
        return
    end
    local names, counts = Metadata.all_collection_names(books)
    if #names == 0 then
        UIManager:show(ConfirmBox:new{
            text = _(
                "Synced books have no collections yet.\n\n"
                    .. "Recompute collections in Calibre, then sync again."
            ),
        })
        return
    end
    local items = {}
    for _, name in ipairs(names) do
        local count = counts[name] or 0
        table.insert(items, {
            text = string.format("%s (%d)", name, count),
            callback = function()
                self:show_books_for_collection(books, name)
            end,
        })
    end
    local menu = Menu:new{
        title = _("Fanfic collections"),
        width = Screen:getWidth(),
        height = Screen:getHeight(),
        show_parent = self.ui,
        item_table = items,
    }
    UIManager:show(menu)
end

function FanficCollections:show_books_for_collection(books, collection_name)
    local matches = Metadata.books_in_collection(books, collection_name)
    if #matches == 0 then
        UIManager:show(ConfirmBox:new{
            text = _("No books in this collection."),
        })
        return
    end
    local items = {}
    local menu
    for _, book in ipairs(matches) do
        local title = book.title or book.lpath or _("Unknown title")
        local authors = ""
        if book.authors and #book.authors > 0 then
            authors = table.concat(book.authors, ", ")
        end
        local label = title
        if authors ~= "" then
            label = string.format("%s - %s", title, authors)
        end
        table.insert(items, {
            text = label,
            callback = function()
                local path = Metadata.resolve_path(book.lpath)
                if not path then
                    UIManager:show(ConfirmBox:new{
                        text = _("Missing book path in collections index."),
                    })
                    return
                end
                if menu then
                    UIManager:close(menu)
                end
                ReaderUI:showReader(path)
            end,
        })
    end
    menu = Menu:new{
        title = collection_name,
        width = Screen:getWidth(),
        height = Screen:getHeight(),
        show_parent = self.ui,
        item_table = items,
    }
    UIManager:show(menu)
end

return FanficCollections
