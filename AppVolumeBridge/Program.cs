using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;

internal static class Program
{
    private static string Lower(string? value) => (value ?? string.Empty).Trim().ToLowerInvariant();

    private static string ProcessName(uint pid)
    {
        if (pid == 0) return string.Empty;
        try { return Process.GetProcessById((int)pid).ProcessName; }
        catch { return string.Empty; }
    }

    private static bool Matches(string haystack, IEnumerable<string> names)
    {
        foreach (var name in names)
        {
            var needle = Lower(name);
            if (!string.IsNullOrWhiteSpace(needle) && haystack.Contains(needle)) return true;
        }
        return false;
    }

    private static void Print(object value) => Console.WriteLine(JsonSerializer.Serialize(value));

    [STAThread]
    private static void Main(string[] args)
    {
        try
        {
            var set = args.Length > 0 && args[0].Equals("set", StringComparison.OrdinalIgnoreCase);
            var listSessions = args.Length > 0 && args[0].Equals("audio-sessions", StringComparison.OrdinalIgnoreCase);
            var volume = 100;
            var nameStart = 1;
            if (set)
            {
                volume = Math.Max(0, Math.Min(100, int.TryParse(args.ElementAtOrDefault(1), out var parsed) ? parsed : 100));
                nameStart = 2;
            }
            else if (args.Length > 0 && args[0].Equals("get", StringComparison.OrdinalIgnoreCase))
            {
                nameStart = 1;
            }

            var names = args.Skip(nameStart).Where(v => !string.IsNullOrWhiteSpace(v)).ToArray();
            var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumerator();
            var hr = enumerator.GetDefaultAudioEndpoint(0, 1, out var device);
            if (hr != 0 || device is null)
            {
                Print(new { ok = false, volume = 100, matched = "", reason = "no-endpoint" });
                return;
            }

            var iid = typeof(IAudioSessionManager2).GUID;
            hr = device.Activate(ref iid, 23, IntPtr.Zero, out var managerPointer);
            if (hr != 0 || managerPointer == IntPtr.Zero)
            {
                Print(new { ok = false, volume = 100, matched = "", reason = "no-manager" });
                return;
            }
            var manager = (IAudioSessionManager2)Marshal.GetObjectForIUnknown(managerPointer);

            hr = manager.GetSessionEnumerator(out var sessions);
            if (hr != 0 || sessions is null)
            {
                Print(new { ok = false, volume = 100, matched = "", reason = "no-sessions" });
                return;
            }

            sessions.GetCount(out var count);
            var matches = new List<(ISimpleAudioVolume Volume, string Label, uint Pid, float Peak)>();
            var diagnostics = new List<object>();
            for (var index = 0; index < count; index++)
            {
                if (sessions.GetSession(index, out var session) != 0 || session is null) continue;
                var control = (IAudioSessionControl2)session;
                control.GetProcessId(out var pid);
                control.GetDisplayName(out var displayName);
                control.GetSessionIdentifier(out var sessionIdentifier);
                control.GetSessionInstanceIdentifier(out var sessionInstanceIdentifier);
                var processName = ProcessName(pid);
                var haystack = Lower(processName + " " + displayName + " " + sessionIdentifier + " " + sessionInstanceIdentifier);
                var peak = 0f;
                try { ((IAudioMeterInformation)session).GetPeakValue(out peak); }
                catch { }
                diagnostics.Add(new { index, processName, displayName, sessionIdentifier, sessionInstanceIdentifier, pid, peak });
                if (!Matches(haystack, names)) continue;

                var simpleVolume = (ISimpleAudioVolume)session;
                var label = (processName + " " + displayName).Trim();
                if (string.IsNullOrWhiteSpace(label)) label = sessionIdentifier;
                matches.Add((simpleVolume, label, pid, peak));
            }

            if (listSessions)
            {
                Print(new { ok = true, count, sessions = diagnostics });
                return;
            }

            if (matches.Count == 0)
            {
                Print(new { ok = false, volume = 100, matched = "", reason = "no-match", count });
                return;
            }

            var eventContext = Guid.Empty;
            if (set)
            {
                foreach (var match in matches)
                    match.Volume.SetMasterVolume(volume / 100f, ref eventContext);
            }
            var active = matches.OrderByDescending(match => match.Peak).ThenByDescending(match => match.Pid != 0).First();
            active.Volume.GetMasterVolume(out var current);
            Print(new
            {
                ok = true,
                volume = (int)Math.Round(Math.Max(0, Math.Min(1, current)) * 100),
                matched = active.Label,
                pid = active.Pid,
                matchedCount = matches.Count,
                sessions = matches.Select(match => new { matched = match.Label, pid = match.Pid, peak = match.Peak }).ToArray()
            });
        }
        catch (Exception ex)
        {
            Print(new { ok = false, volume = 100, matched = "", reason = ex.GetType().Name + ": " + ex.Message });
        }
    }
}

[ComImport]
[Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
internal class MMDeviceEnumerator { }

[ComImport]
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IMMDeviceEnumerator
{
    int NotImpl1();
    [PreserveSig] int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice device);
}

[ComImport]
[Guid("D666063F-1587-4E43-81F1-B948E807363F")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IMMDevice
{
    [PreserveSig] int Activate(ref Guid iid, int clsCtx, IntPtr activationParams, out IntPtr interfacePointer);
}

[ComImport]
[Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IAudioSessionManager2
{
    int NotImpl1();
    int NotImpl2();
    [PreserveSig] int GetSessionEnumerator(out IAudioSessionEnumerator sessionEnum);
}

[ComImport]
[Guid("E2F5BB11-0570-40CA-ACDD-3AA01277DEE8")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IAudioSessionEnumerator
{
    [PreserveSig] int GetCount(out int sessionCount);
    [PreserveSig] int GetSession(int sessionCount, out IAudioSessionControl session);
}

[ComImport]
[Guid("F4B1A599-7266-4319-A8CA-E70ACB11E8CD")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IAudioSessionControl
{
    [PreserveSig] int GetState(out int state);
    [PreserveSig] int GetDisplayName([MarshalAs(UnmanagedType.LPWStr)] out string displayName);
    [PreserveSig] int SetDisplayName([MarshalAs(UnmanagedType.LPWStr)] string displayName, ref Guid eventContext);
    [PreserveSig] int GetIconPath([MarshalAs(UnmanagedType.LPWStr)] out string iconPath);
    [PreserveSig] int SetIconPath([MarshalAs(UnmanagedType.LPWStr)] string iconPath, ref Guid eventContext);
    [PreserveSig] int GetGroupingParam(out Guid groupingParam);
    [PreserveSig] int SetGroupingParam(ref Guid groupingParam, ref Guid eventContext);
    [PreserveSig] int RegisterAudioSessionNotification(IntPtr client);
    [PreserveSig] int UnregisterAudioSessionNotification(IntPtr client);
}

[ComImport]
[Guid("bfb7ff88-7239-4fc9-8fa2-07c950be9c6d")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IAudioSessionControl2
{
    [PreserveSig] int GetState(out int state);
    [PreserveSig] int GetDisplayName([MarshalAs(UnmanagedType.LPWStr)] out string displayName);
    [PreserveSig] int SetDisplayName([MarshalAs(UnmanagedType.LPWStr)] string displayName, ref Guid eventContext);
    [PreserveSig] int GetIconPath([MarshalAs(UnmanagedType.LPWStr)] out string iconPath);
    [PreserveSig] int SetIconPath([MarshalAs(UnmanagedType.LPWStr)] string iconPath, ref Guid eventContext);
    [PreserveSig] int GetGroupingParam(out Guid groupingParam);
    [PreserveSig] int SetGroupingParam(ref Guid groupingParam, ref Guid eventContext);
    [PreserveSig] int RegisterAudioSessionNotification(IntPtr client);
    [PreserveSig] int UnregisterAudioSessionNotification(IntPtr client);
    [PreserveSig] int GetSessionIdentifier([MarshalAs(UnmanagedType.LPWStr)] out string sessionIdentifier);
    [PreserveSig] int GetSessionInstanceIdentifier([MarshalAs(UnmanagedType.LPWStr)] out string sessionInstanceIdentifier);
    [PreserveSig] int GetProcessId(out uint processId);
    [PreserveSig] int IsSystemSoundsSession();
    [PreserveSig] int SetDuckingPreference(bool optOut);
}

[ComImport]
[Guid("C02216F6-8C67-4B5B-9D00-D008E73E0064")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface IAudioMeterInformation
{
    [PreserveSig] int GetPeakValue(out float peak);
    [PreserveSig] int GetMeteringChannelCount(out int channelCount);
    [PreserveSig] int GetChannelsPeakValues(int channelCount, [Out] float[] peaks);
    [PreserveSig] int QueryHardwareSupport(out int hardwareSupportMask);
}

[ComImport]
[Guid("87CE5498-68D6-44E5-9215-6DA47EF883D8")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
internal interface ISimpleAudioVolume
{
    [PreserveSig] int SetMasterVolume(float level, ref Guid eventContext);
    [PreserveSig] int GetMasterVolume(out float level);
    [PreserveSig] int SetMute(bool mute, ref Guid eventContext);
    [PreserveSig] int GetMute(out bool mute);
}
