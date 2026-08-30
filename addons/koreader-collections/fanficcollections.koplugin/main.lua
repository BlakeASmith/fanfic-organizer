local BookList = require("ui/widget/booklist")
local ConfirmBox = require("ui/widget/confirmbox")
local Device = require("device")
local DocumentRegistry = require("document/documentregistry")
local Event = require("ui/event")
local Menu = require("ui/widget/menu")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local filemanagerutil = require("apps/filemanager/filemanagerutil")
local logger = require("logger")
local _ = require("gettext")

local Errors = require("errors")
local Metadata = require("metadata")

local Screen = Device.screen

local FanficCollections = WidgetContainer:extend{
    name = "fanficcollections",
    is_doc_only = false,
}

function FanficCollections:show_error(context, user_text, debug_text)
    Errors.show(context, user_text, debug_text)
end

function FanficCollections:run_action(context, user_text, fn)
    return Errors.guard(context, user_text, fn)
end

function FanficCollections:init()
    if not self.ui or not self.ui.menu then
        self._load_error = "ui/menu unavailable during init"
        logger.err("fanficcollections:", self._load_error)
        return
    end
    local ok, err = pcall(function()
        self.ui.menu:registerToMainMenu(self)
    end)
    if not ok then
        logger.err("fanficcollections: init failed:", err)
        self._load_error = tostring(err)
    end
end

function FanficCollections:addToMainMenu(menu_items)
    if self.ui.document then
        return
    end
    menu_items.fanfic_collections = {
        text = _("Fanfic collections"),
        sorting_hint = "search",
        callback = function()
            self:run_action("menu", _("Fanfic collections could not open."), function()
                self:show_collection_picker()
            end)
        end,
    }
end

function FanficCollections:close_menu(menu)
    if not menu then
        return
    end
    pcall(function()
        if menu.onClose then
            menu:onClose()
        end
        UIManager:close(menu)
    end)
end

function FanficCollections:close_all_menus()
    self:close_menu(self._books_menu)
    self._books_menu = nil
    self:close_menu(self._collection_menu)
    self._collection_menu = nil
end

function FanficCollections:open_book(path, debug_text)
    if not path then
        self:show_error(
            "open_book",
            _("Could not find this book on the device."),
            debug_text
        )
        return
    end
    if not DocumentRegistry:hasProvider(path) then
        self:show_error(
            "open_book",
            _("Could not open this file."),
            debug_text or path
        )
        return
    end
    if self.ui.document then
        local ok, err = pcall(function()
            self.ui:switchDocument(path)
        end)
        if not ok then
            self:show_error("open_book", _("Could not open this book."), tostring(err) .. "\n" .. path)
        end
        return
    end
    local function pre_callback()
        UIManager:broadcastEvent(Event:new("SetupShowReader"))
        self:close_all_menus()
    end
    local ok, err = pcall(function()
        filemanagerutil.openFile(self.ui, path, pre_callback, true)
    end)
    if not ok then
        self:show_error("open_book", _("Could not open this book."), tostring(err) .. "\n" .. path)
    end
end

function FanficCollections:show_collection_picker()
    if self._load_error then
        self:show_error(
            "init",
            _("Fanfic collections did not finish loading."),
            self._load_error
        )
        return
    end
    local books, load_debug = Metadata.load_books()
    if Metadata.last_error then
        self:show_error(
            "load_books",
            _("Could not read fanfic collections data."),
            Metadata.last_error .. "\n" .. Metadata.json_path()
        )
        return
    end
    if #books == 0 then
        UIManager:show(ConfirmBox:new{
            text = _(
                "No fanfic-organizer collections found.\n\n"
                    .. "Connect your Kobo to Calibre, run Fanfic Organizer "
                    .. "→ Deploy to KOReader… after sync."
            ) .. (load_debug and ("\n\n" .. load_debug) or ""),
        })
        return
    end
    local names, counts = Metadata.all_collection_names(books)
    if #names == 0 then
        UIManager:show(ConfirmBox:new{
            text = _(
                "Synced books have no collections yet.\n\n"
                    .. "Recompute collections in Calibre, then deploy again."
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
                self:run_action("collection", _("Could not list books in this collection."), function()
                    self:show_books_for_collection(books, name)
                end)
            end,
        })
    end
    local ok, err = pcall(function()
        self._collection_menu = Menu:new{
            title = _("Fanfic collections"),
            width = Screen:getWidth(),
            height = Screen:getHeight(),
            show_parent = self.ui,
            item_table = items,
        }
        UIManager:show(self._collection_menu)
    end)
    if not ok then
        self:show_error("collection_menu", _("Could not show collections."), tostring(err))
    end
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
    for _, book in ipairs(matches) do
        local path, debug_text = Metadata.resolve_path(book)
        local title = book.title or book.lpath or _("Unknown title")
        local authors = book.authors
        local author_text = ""
        if type(authors) == "table" and #authors > 0 then
            author_text = table.concat(authors, ", ")
        end
        local label = title
        if author_text ~= "" then
            label = string.format("%s - %s", title, author_text)
        end
        table.insert(items, {
            text = label,
            file = path,
            _debug = debug_text,
        })
    end
    local ok, err = pcall(function()
        local menu
        menu = BookList:new{
            title = collection_name,
            show_parent = self.ui,
            item_table = items,
            onMenuSelect = function(_, item)
                self:run_action("open_book", _("Could not open this book."), function()
                    self:open_book(item.file, item._debug)
                end)
            end,
        }
        self._books_menu = menu
        UIManager:show(menu)
    end)
    if not ok then
        self:show_error("books_menu", _("Could not show books in this collection."), tostring(err))
    end
end

return FanficCollections
