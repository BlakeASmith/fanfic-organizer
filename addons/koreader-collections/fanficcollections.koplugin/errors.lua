local ConfirmBox = require("ui/widget/confirmbox")
local UIManager = require("ui/uimanager")
local logger = require("logger")
local _ = require("gettext")

local Errors = {}

function Errors.log(context, detail)
    if detail == nil or detail == "" then
        logger.err("fanficcollections:", context)
    else
        logger.err("fanficcollections:", context, detail)
    end
end

function Errors.show(context, user_text, debug_text)
    Errors.log(context, debug_text)
    local text = user_text
    if debug_text and debug_text ~= "" then
        text = text .. "\n\n" .. debug_text
    end
    UIManager:show(ConfirmBox:new{
        text = text,
    })
end

function Errors.guard(context, user_text, fn, ...)
    local ok, result = pcall(fn, ...)
    if ok then
        return true, result
    end
    Errors.show(context, user_text, tostring(result))
    return false, nil
end

return Errors
