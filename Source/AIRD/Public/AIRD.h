#pragma once

#include "CoreMinimal.h"
#include "Delegates/Delegate.h"
#include "Modules/ModuleManager.h"

class FAIRDModule : public IModuleInterface
{
public:
    virtual void StartupModule() override;
    virtual void ShutdownModule() override;

private:
    bool TryAutoStartMcpServer();
    void InitializeRuntimeBridgeBootstrap();
    void HandlePythonInitialized();
    bool TryStartUnrealRuntimeBridge();
    bool IsServerReachable(const FString& Host, int32 Port) const;
    void LoadServerSettings(FString& OutHost, int32& OutPort, bool& bOutAutoStart) const;
    FString ResolvePythonExecutable() const;
    bool ExecutePythonScriptByPath(const FString& ScriptPath) const;
    bool StartServerProcess(
        const FString& PythonExecutable,
        const FString& ScriptPath,
        const FString& WorkingDirectory,
        uint32& OutPid) const;

    FDelegateHandle PythonInitializedDelegateHandle;
    bool bRuntimeBridgeBootstrapAttempted = false;
    bool bRuntimeBridgeStarted = false;
};
