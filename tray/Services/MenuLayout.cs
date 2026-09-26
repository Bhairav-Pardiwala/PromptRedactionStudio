using System.Collections.Generic;

namespace PromptRedactionTray.Services;

/// <summary>
/// Keeps optional tray-menu items present or absent.
///
/// Hiding an item with IsVisible depends on each platform's native menu honouring the
/// flag, and the tray menu is a Win32 menu on Windows and an NSMenu on macOS. Adding and
/// removing the item does not depend on anything. Generic over the item type so it can
/// be tested without starting Avalonia.
/// </summary>
public static class MenuLayout
{
    /// <summary>Make <paramref name="item"/> present right after <paramref name="after"/>, or absent.</summary>
    public static void SyncOptionalItem<T>(IList<T> items, T item, T after, bool show)
        where T : class
    {
        var present = items.Contains(item);
        if (show && !present)
        {
            var anchor = items.IndexOf(after);
            items.Insert(anchor < 0 ? items.Count : anchor + 1, item);
        }
        else if (!show && present)
        {
            items.Remove(item);
        }
    }
}
