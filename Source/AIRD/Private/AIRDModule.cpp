#include "AIRD.h"
#include "AIRDLog.h"

#include "HAL/PlatformProcess.h"
#include "Interfaces/IPluginManager.h"
#include "IPythonScriptPlugin.h"
#include "Dom/JsonObject.h"
#include "Misc/FileHelper.h"
#include "Misc/ConfigCacheIni.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"
#include "PythonScriptTypes.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "SocketSubsystem.h"
#include "Sockets.h"

DEFINE_LOG_CATEGORY(LogAIRD);

#define LOCTEXT_NAMESPACE "FAIRDModule"

namespace
{
constexpr int32 DefaultMcpPort = 8765;
constexpr TCHAR DefaultMcpHost[] = TEXT("127.0.0.1");
constexpr TCHAR AirdSettingsSection[] = TEXT("AIRD.Settings");
constexpr TCHAR AutoStartKey[] = TEXT("AutoStartMCP");
constexpr TCHAR EmbeddedPythonRelativePath[] = TEXT("Binaries/ThirdParty/Python3/Win64/python.exe");
constexpr TCHAR RuntimeConfigPath[] = TEXT("config.json");
constexpr TCHAR RuntimePortKey[] = TEXT("mcp_websocket_port");
constexpr TCHAR RuntimeBridgeBootstrapRelativePath[] = TEXT("Scripts/start_runtime_bridge_in_unreal.py");
constexpr TCHAR RuntimeBridgeRootRelativePath[] = TEXT("memory/runtime_bridge");
constexpr TCHAR RuntimeBridgeHeartbeatRelativePath[] = TEXT("memory/runtime_bridge/heartbeat.json");
}

void FAIRDModule::StartupModule()
{
    UE_LOG(LogAIRD, Log, TEXT("AIRD runtime bridge bootstrap start (StartupModule)."));

    if (!FModuleManager::Get().IsModuleLoaded(TEXT("PythonScriptPlugin")))
    {
        UE_LOG(LogAIRD, Warning, TEXT("PythonScriptPlugin is not loaded; AIRD Python features may fail."));
    }
    if (!FModuleManager::Get().IsModuleLoaded(TEXT("WebBrowserWidget")))
    {
        UE_LOG(LogAIRD, Warning, TEXT("WebBrowserWidget is not loaded; AIRD panel Web UI may fail."));
    }

    TryAutoStartMcpServer();
    InitializeRuntimeBridgeBootstrap();

    if (TSharedPtr<IPlugin> P = IPluginManager::Get().FindPlugin(TEXT("AIRD")); P.IsValid())
    {
        UE_LOG(LogAIRD, Log, TEXT("AIRD module started (plugin %s)."), *P->GetDescriptor().VersionName);
    }
    else
    {
        UE_LOG(LogAIRD, Log, TEXT("AIRD module started."));
    }
}

void FAIRDModule::ShutdownModule()
{
    if (PythonInitializedDelegateHandle.IsValid())
    {
        if (IPythonScriptPlugin* PythonModule = IPythonScriptPlugin::Get())
        {
            PythonModule->OnPythonInitialized().Remove(PythonInitializedDelegateHandle);
        }
        PythonInitializedDelegateHandle.Reset();
    }
}

bool FAIRDModule::TryAutoStartMcpServer()
{
#if PLATFORM_WINDOWS
    bool bAutoStart = true;
    FString Host;
    int32 Port = DefaultMcpPort;
    LoadServerSettings(Host, Port, bAutoStart);

    if (!bAutoStart)
    {
        UE_LOG(LogAIRD, Log, TEXT("AIRD MCP auto-start disabled by config."));
        return false;
    }

    if (IsServerReachable(Host, Port))
    {
        UE_LOG(LogAIRD, Log, TEXT("AIRD MCP server already running (%s:%d)."), *Host, Port);
        return true;
    }

    const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("AIRD"));
    if (!Plugin.IsValid())
    {
        UE_LOG(LogAIRD, Error, TEXT("AIRD MCP failed to start server: plugin path is unavailable."));
        return false;
    }

    const FString ScriptPath = FPaths::ConvertRelativePathToFull(
        FPaths::Combine(Plugin->GetBaseDir(), TEXT("Content/Python/server.py")));
    if (!FPaths::FileExists(ScriptPath))
    {
        UE_LOG(LogAIRD, Error, TEXT("AIRD MCP failed to start server: script missing (%s)."), *ScriptPath);
        return false;
    }

    const FString PythonExecutable = ResolvePythonExecutable();
    uint32 StartedPid = 0;
    if (!StartServerProcess(PythonExecutable, ScriptPath, FPaths::GetPath(ScriptPath), StartedPid))
    {
        UE_LOG(
            LogAIRD,
            Error,
            TEXT("AIRD MCP failed to start server: could not launch python (%s)."),
            *PythonExecutable);
        return false;
    }

    constexpr int32 MaxAttempts = 20;
    constexpr float RetryDelaySec = 0.25f;
    for (int32 Attempt = 0; Attempt < MaxAttempts; ++Attempt)
    {
        FPlatformProcess::Sleep(RetryDelaySec);
        if (IsServerReachable(Host, Port))
        {
            UE_LOG(
                LogAIRD,
                Log,
                TEXT("AIRD MCP server started successfully (pid=%u, %s:%d)."),
                StartedPid,
                *Host,
                Port);
            return true;
        }
    }

    UE_LOG(
        LogAIRD,
        Error,
        TEXT("AIRD MCP failed to start server: endpoint not reachable after launch (pid=%u, %s:%d)."),
        StartedPid,
        *Host,
        Port);
    return false;
#else
    UE_LOG(LogAIRD, Warning, TEXT("AIRD MCP auto-start is currently supported only on Windows."));
    return false;
#endif
}

void FAIRDModule::InitializeRuntimeBridgeBootstrap()
{
#if WITH_EDITOR
    UE_LOG(LogAIRD, Log, TEXT("AIRD runtime bridge bootstrap started."));

    IPythonScriptPlugin* PythonModule = FModuleManager::LoadModulePtr<IPythonScriptPlugin>(TEXT("PythonScriptPlugin"));
    if (!PythonModule)
    {
        UE_LOG(LogAIRD, Error, TEXT("AIRD runtime bridge bootstrap failed: PythonScriptPlugin module is unavailable."));
        return;
    }

    UE_LOG(
        LogAIRD,
        Log,
        TEXT("AIRD runtime bridge python state: available=%s configured=%s initialized=%s"),
        PythonModule->IsPythonAvailable() ? TEXT("true") : TEXT("false"),
        PythonModule->IsPythonConfigured() ? TEXT("true") : TEXT("false"),
        PythonModule->IsPythonInitialized() ? TEXT("true") : TEXT("false"));

    if (!PythonModule->IsPythonInitialized())
    {
        const bool bForceEnableRequested = PythonModule->ForceEnablePythonAtRuntime();
        UE_LOG(
            LogAIRD,
            Log,
            TEXT("AIRD runtime bridge requested ForceEnablePythonAtRuntime => %s"),
            bForceEnableRequested ? TEXT("true") : TEXT("false"));
    }

    if (PythonModule->IsPythonInitialized())
    {
        HandlePythonInitialized();
        return;
    }

    if (!PythonInitializedDelegateHandle.IsValid())
    {
        PythonInitializedDelegateHandle = PythonModule->OnPythonInitialized().AddRaw(
            this,
            &FAIRDModule::HandlePythonInitialized);
    }

    UE_LOG(
        LogAIRD,
        Warning,
        TEXT("AIRD runtime bridge waiting for Python initialization before executing bootstrap script."));
#endif
}

void FAIRDModule::HandlePythonInitialized()
{
#if WITH_EDITOR
    if (bRuntimeBridgeStarted)
    {
        UE_LOG(LogAIRD, Verbose, TEXT("AIRD runtime bridge already started; skipping duplicate bootstrap."));
        return;
    }

    UE_LOG(LogAIRD, Log, TEXT("AIRD runtime bridge python initialized callback fired."));
    bRuntimeBridgeBootstrapAttempted = true;
    bRuntimeBridgeStarted = TryStartUnrealRuntimeBridge();

    if (bRuntimeBridgeStarted && PythonInitializedDelegateHandle.IsValid())
    {
        if (IPythonScriptPlugin* PythonModule = IPythonScriptPlugin::Get())
        {
            PythonModule->OnPythonInitialized().Remove(PythonInitializedDelegateHandle);
        }
        PythonInitializedDelegateHandle.Reset();
    }
#endif
}

bool FAIRDModule::TryStartUnrealRuntimeBridge()
{
#if WITH_EDITOR
    UE_LOG(LogAIRD, Log, TEXT("AIRD runtime bridge bootstrap start: resolving plugin and script paths."));

    const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("AIRD"));
    if (!Plugin.IsValid())
    {
        UE_LOG(LogAIRD, Error, TEXT("AIRD runtime bridge failed to start: plugin path is unavailable."));
        return false;
    }

    const FString PluginBaseDir = FPaths::ConvertRelativePathToFull(Plugin->GetBaseDir());
    const FString ScriptPath = FPaths::ConvertRelativePathToFull(
        FPaths::Combine(Plugin->GetBaseDir(), RuntimeBridgeBootstrapRelativePath));
    const FString RuntimeBridgeRootPath = FPaths::ConvertRelativePathToFull(
        FPaths::Combine(Plugin->GetBaseDir(), RuntimeBridgeRootRelativePath));
    const FString RuntimeBridgeHeartbeatPath = FPaths::ConvertRelativePathToFull(
        FPaths::Combine(Plugin->GetBaseDir(), RuntimeBridgeHeartbeatRelativePath));

    UE_LOG(LogAIRD, Log, TEXT("AIRD runtime bridge plugin base path = %s"), *PluginBaseDir);
    UE_LOG(LogAIRD, Log, TEXT("AIRD runtime bridge bootstrap script path = %s"), *ScriptPath);
    UE_LOG(LogAIRD, Log, TEXT("AIRD runtime bridge root path = %s"), *RuntimeBridgeRootPath);
    UE_LOG(LogAIRD, Log, TEXT("AIRD runtime bridge heartbeat path = %s"), *RuntimeBridgeHeartbeatPath);

    if (!FPaths::FileExists(ScriptPath))
    {
        UE_LOG(LogAIRD, Error, TEXT("AIRD runtime bridge bootstrap script is missing (%s)."), *ScriptPath);
        return false;
    }

    const bool bExecuted = ExecutePythonScriptByPath(ScriptPath);
    if (bExecuted)
    {
        UE_LOG(LogAIRD, Log, TEXT("AIRD runtime bridge bootstrap command executed (%s)."), *ScriptPath);
        return true;
    }

    UE_LOG(LogAIRD, Warning, TEXT("AIRD runtime bridge bootstrap command failed (%s)."), *ScriptPath);
    return false;
#else
    return false;
#endif
}

bool FAIRDModule::IsServerReachable(const FString& Host, int32 Port) const
{
    if (Port <= 0)
    {
        return false;
    }

    ISocketSubsystem* SocketSubsystem = ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);
    if (!SocketSubsystem)
    {
        return false;
    }

    bool bAddressValid = false;
    TSharedRef<FInternetAddr> Address = SocketSubsystem->CreateInternetAddr();
    Address->SetIp(*Host, bAddressValid);
    if (!bAddressValid)
    {
        Address->SetIp(DefaultMcpHost, bAddressValid);
    }
    if (!bAddressValid)
    {
        return false;
    }

    Address->SetPort(Port);
    FSocket* Socket = SocketSubsystem->CreateSocket(NAME_Stream, TEXT("AIRDServerProbe"), false);
    if (!Socket)
    {
        return false;
    }

    Socket->SetNonBlocking(false);
    const bool bConnected = Socket->Connect(*Address);
    SocketSubsystem->DestroySocket(Socket);
    return bConnected;
}

void FAIRDModule::LoadServerSettings(FString& OutHost, int32& OutPort, bool& bOutAutoStart) const
{
    OutHost = DefaultMcpHost;
    OutPort = DefaultMcpPort;
    bOutAutoStart = true;

    if (!GConfig)
    {
        return;
    }

    FString PluginConfigPath;
    if (const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("AIRD")); Plugin.IsValid())
    {
        PluginConfigPath = FPaths::Combine(Plugin->GetBaseDir(), TEXT("Config/DefaultAIRD.ini"));
        const FString RuntimeConfigFile = FPaths::Combine(Plugin->GetBaseDir(), RuntimeConfigPath);

        FString RuntimeConfigContent;
        if (FFileHelper::LoadFileToString(RuntimeConfigContent, *RuntimeConfigFile))
        {
            TSharedPtr<FJsonObject> RootObject;
            const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(RuntimeConfigContent);
            if (FJsonSerializer::Deserialize(Reader, RootObject) && RootObject.IsValid())
            {
                int32 RuntimePort = 0;
                if (RootObject->TryGetNumberField(RuntimePortKey, RuntimePort) && RuntimePort > 0)
                {
                    OutPort = RuntimePort;
                }
                else
                {
                    FString RuntimePortText;
                    if (RootObject->TryGetStringField(RuntimePortKey, RuntimePortText))
                    {
                        const int32 ParsedPort = FCString::Atoi(*RuntimePortText);
                        if (ParsedPort > 0)
                        {
                            OutPort = ParsedPort;
                        }
                    }
                }
            }
        }
    }

    if (!PluginConfigPath.IsEmpty() && FPaths::FileExists(PluginConfigPath))
    {
        GConfig->GetBool(AirdSettingsSection, AutoStartKey, bOutAutoStart, PluginConfigPath);
    }

    bool bIniAutoStart = false;
    if (GConfig->GetBool(AirdSettingsSection, AutoStartKey, bIniAutoStart, GEngineIni))
    {
        bOutAutoStart = bIniAutoStart;
    }

    if (OutHost.IsEmpty())
    {
        OutHost = DefaultMcpHost;
    }
    if (OutPort <= 0)
    {
        OutPort = DefaultMcpPort;
    }
}

FString FAIRDModule::ResolvePythonExecutable() const
{
    const FString EmbeddedPython = FPaths::ConvertRelativePathToFull(
        FPaths::Combine(FPaths::EngineDir(), EmbeddedPythonRelativePath));
    if (FPaths::FileExists(EmbeddedPython))
    {
        return EmbeddedPython;
    }

    return TEXT("python");
}

bool FAIRDModule::ExecutePythonScriptByPath(const FString& ScriptPath) const
{
#if WITH_EDITOR
    if (ScriptPath.IsEmpty())
    {
        return false;
    }

    IPythonScriptPlugin* PythonModule = FModuleManager::LoadModulePtr<IPythonScriptPlugin>(TEXT("PythonScriptPlugin"));
    if (!PythonModule)
    {
        UE_LOG(LogAIRD, Warning, TEXT("PythonScriptPlugin unavailable; cannot execute %s"), *ScriptPath);
        return false;
    }
    if (!PythonModule->IsPythonInitialized())
    {
        UE_LOG(
            LogAIRD,
            Warning,
            TEXT("AIRD runtime bridge python script execution blocked: Python is not initialized yet (available=%s configured=%s initialized=%s)."),
            PythonModule->IsPythonAvailable() ? TEXT("true") : TEXT("false"),
            PythonModule->IsPythonConfigured() ? TEXT("true") : TEXT("false"),
            PythonModule->IsPythonInitialized() ? TEXT("true") : TEXT("false"));
        return false;
    }

    FString EscapedPath = ScriptPath;
    EscapedPath.ReplaceInline(TEXT("\\"), TEXT("/"));
    EscapedPath.ReplaceInline(TEXT("'"), TEXT("\\'"));

    const FString Command = FString::Printf(
        TEXT("import runpy; runpy.run_path(r'%s', run_name='__main__')"),
        *EscapedPath);

    UE_LOG(LogAIRD, Log, TEXT("AIRD runtime bridge python script execution started: %s"), *ScriptPath);
    const bool bExecuted = PythonModule->ExecPythonCommand(*Command);
    UE_LOG(
        LogAIRD,
        Log,
        TEXT("AIRD runtime bridge python script execution %s: %s"),
        bExecuted ? TEXT("succeeded") : TEXT("failed"),
        *ScriptPath);
    return bExecuted;
#else
    return false;
#endif
}

bool FAIRDModule::StartServerProcess(
    const FString& PythonExecutable,
    const FString& ScriptPath,
    const FString& WorkingDirectory,
    uint32& OutPid) const
{
    OutPid = 0;
    if (PythonExecutable.IsEmpty() || ScriptPath.IsEmpty())
    {
        return false;
    }

    const FString Arguments = FString::Printf(TEXT("\"%s\""), *ScriptPath);
    FProcHandle ProcessHandle = FPlatformProcess::CreateProc(
        *PythonExecutable,
        *Arguments,
        true,
        true,
        true,
        &OutPid,
        0,
        *WorkingDirectory,
        nullptr,
        nullptr);

    if (!ProcessHandle.IsValid())
    {
        return false;
    }

    FPlatformProcess::CloseProc(ProcessHandle);
    return true;
}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FAIRDModule, AIRD)
