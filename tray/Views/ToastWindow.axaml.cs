using System;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Markup.Xaml;
using Avalonia.Media;
using Avalonia.Threading;

namespace PromptRedactionTray.Views;

/// <summary>
/// A small auto-dismissing notification near the tray.
///
/// Avalonia has no cross-platform native notification API, and a plain window behaves
/// identically everywhere -- which matters more here than looking native, because the
/// toast is the only feedback the user gets for an action with no visible window.
/// </summary>
public partial class ToastWindow : Window
{
    private static ToastWindow? _current;

    private DispatcherTimer? _timer;

    public ToastWindow()
    {
        // InitializeComponent comes from Avalonia's name generator; declaring one here
        // would shadow it and leave every x:Name'd control null. See NoShadowedInitializeComponent.
        InitializeComponent();
    }

    public static void Show(string title, string body, bool isError = false)
    {
        // Replace any toast already on screen rather than stacking them up.
        _current?.Close();

        var toast = new ToastWindow();
        toast.FindControl<TextBlock>("TitleText")!.Text = title;
        toast.FindControl<TextBlock>("BodyText")!.Text = body;

        if (isError)
        {
            var root = toast.FindControl<Border>("Root")!;
            root.BorderBrush = new SolidColorBrush(Color.Parse("#FF6B5E"));
            toast.FindControl<TextBlock>("TitleText")!.Foreground =
                new SolidColorBrush(Color.Parse("#FF6B5E"));
        }

        _current = toast;
        toast.Closed += (_, _) =>
        {
            if (ReferenceEquals(_current, toast))
            {
                _current = null;
            }
        };

        toast.Show();
        toast.PositionNearTray();

        // Errors stay long enough to read; confirmations get out of the way quickly.
        toast._timer = new DispatcherTimer
        {
            Interval = TimeSpan.FromSeconds(isError ? 7 : 3.5),
        };
        toast._timer.Tick += (_, _) =>
        {
            toast._timer?.Stop();
            toast.Close();
        };
        toast._timer.Start();
    }

    /// <summary>Bottom-right of the working area, which is where the tray is on most setups.</summary>
    private void PositionNearTray()
    {
        var screen = Screens.ScreenFromWindow(this) ?? Screens.Primary;
        if (screen is null)
        {
            return;
        }

        var area = screen.WorkingArea;
        var scale = screen.Scaling;
        var width = (int)(Bounds.Width * scale);
        var height = (int)(Bounds.Height * scale);
        var margin = (int)(16 * scale);

        Position = new PixelPoint(
            area.X + area.Width - width - margin,
            area.Y + area.Height - height - margin);
    }

    protected override void OnPointerPressed(Avalonia.Input.PointerPressedEventArgs e)
    {
        // Click to dismiss early.
        _timer?.Stop();
        Close();
        base.OnPointerPressed(e);
    }
}
