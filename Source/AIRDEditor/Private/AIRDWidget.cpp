#include "AIRDEditor.h"

#include "AIRDBridge.h"
#include "Interfaces/IPluginManager.h"
#include "Misc/Paths.h"
#include "Widgets/Docking/SDockTab.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SSpacer.h"
#include "Widgets/Text/STextBlock.h"
#include "Widgets/SBoxPanel.h"
#include "SWebBrowser.h"

static bool GAirdEngineRequestedRunning = false;

static FReply OnStartAirdEngineClicked()
{
    const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("AIRD"));
    if (!Plugin.IsValid())
    {
        UE_LOG(LogTemp, Warning, TEXT("Start AIRD Engine: plugin root not found."));
        return FReply::Handled();
    }

    const FString PythonDir = FPaths::ConvertRelativePathToFull(Plugin->GetBaseDir() / TEXT("Content/Python")).Replace(TEXT("\\"), TEXT("/"));
    const FString ScriptDir = FPaths::ConvertRelativePathToFull(Plugin->GetBaseDir() / TEXT("Scripts")).Replace(TEXT("\\"), TEXT("/"));
    const FString BootstrapScript = FPaths::ConvertRelativePathToFull(Plugin->GetBaseDir() / TEXT("Scripts/run_mcp_in_unreal.py")).Replace(TEXT("\\"), TEXT("/"));

    FString PythonCommand;
    if (!GAirdEngineRequestedRunning)
    {
        PythonCommand = FString::Printf(
            TEXT("import sys,runpy,threading,time\n")
            TEXT("import unreal\n")
            TEXT("try:\n")
            TEXT("    if hasattr(unreal, 'get_editor_subsystem') and hasattr(unreal, 'UnrealEditorSubsystem'):\n")
            TEXT("        _ed = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)\n")
            TEXT("        if _ed:\n")
            TEXT("            _ = _ed.get_editor_world()\n")
            TEXT("    elif hasattr(unreal, 'EditorLevelLibrary'):\n")
            TEXT("        _ = unreal.EditorLevelLibrary.get_editor_world()\n")
            TEXT("except Exception as _ctx_err:\n")
            TEXT("    unreal.log_warning('[AIRD] editor context probe warning: ' + str(_ctx_err))\n")
            TEXT("p=r'%s'\n")
            TEXT("s=r'%s'\n")
            TEXT("if p not in sys.path:\n")
            TEXT("    sys.path.insert(0,p)\n")
            TEXT("if s not in sys.path:\n")
            TEXT("    sys.path.insert(0,s)\n")
            TEXT("runpy.run_path(r'%s', run_name='__main__')\n")
            TEXT("try:\n")
            TEXT("    import mcp_server\n")
            TEXT("    mcp_server.update_scene_context_async(20.0)\n")
            TEXT("    unreal.log('[AIRD] start requested (non-blocking).')\n")
            TEXT("except Exception as e:\n")
            TEXT("    unreal.log_warning('[AIRD] async scene sync failed: ' + str(e))\n"),
            *PythonDir,
            *ScriptDir,
            *BootstrapScript
        );
    }
    else
    {
        PythonCommand =
            TEXT("import unreal\n")
            TEXT("try:\n")
            TEXT("    import mcp_server\n")
            TEXT("    stopped = mcp_server.stop_mcp_server(1.0)\n")
            TEXT("    unreal.log('[AIRD] stop_mcp_server=' + str(stopped))\n")
            TEXT("except Exception as e:\n")
            TEXT("    unreal.log_warning('[AIRD] stop failed: ' + str(e))\n");
    }

    const bool bExecuted = UAIRDBridge::ExecutePythonCommand(PythonCommand);
    if (bExecuted)
    {
        GAirdEngineRequestedRunning = !GAirdEngineRequestedRunning;
        UE_LOG(LogTemp, Log, TEXT("AIRD Engine toggle command executed. running=%s"), GAirdEngineRequestedRunning ? TEXT("true") : TEXT("false"));
    }
    else
    {
        UE_LOG(LogTemp, Warning, TEXT("AIRD Engine toggle command blocked/failed."));
    }

    return FReply::Handled();
}

TSharedRef<SDockTab> SpawnAIRDWidgetTab(const FSpawnTabArgs& Args)
{
    const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("AIRD"));
    FString HtmlPath;
    if (Plugin.IsValid())
    {
        const FString RootHtml = FPaths::ConvertRelativePathToFull(Plugin->GetBaseDir() / TEXT("008.html"));
        const FString ContentHtml = FPaths::ConvertRelativePathToFull(Plugin->GetBaseDir() / TEXT("Content/UI/008.html"));
        HtmlPath = FPaths::FileExists(RootHtml) ? RootHtml : ContentHtml;
        UE_LOG(LogTemp, Log, TEXT("AIRD UI HTML path selected: %s"), *HtmlPath);
    }

    const FString Url = HtmlPath.IsEmpty() ? TEXT("about:blank") : FString(TEXT("file:///")) + HtmlPath.Replace(TEXT("\\"), TEXT("/"));

    return SNew(SDockTab)
        .TabRole(ETabRole::NomadTab)
        [
            SNew(SBorder)
            .Padding(2.0f)
            [
                SNew(SVerticalBox)
                + SVerticalBox::Slot()
                .AutoHeight()
                .Padding(4.0f)
                [
                    SNew(SHorizontalBox)
                    + SHorizontalBox::Slot()
                    .AutoWidth()
                    .VAlign(VAlign_Center)
                    [
                        SNew(STextBlock)
                        .Text(FText::FromString(TEXT("AIRD - AI-Ready Development Assistant")))
                    ]
                    + SHorizontalBox::Slot()
                    .FillWidth(1.0f)
                    [
                        SNew(SSpacer)
                    ]
                    + SHorizontalBox::Slot()
                    .AutoWidth()
                    .VAlign(VAlign_Center)
                    [
                        SNew(SButton)
                        .Text_Lambda([]() -> FText
                        {
                            return GAirdEngineRequestedRunning
                                ? FText::FromString(TEXT("Stop AIRD Engine"))
                                : FText::FromString(TEXT("Start AIRD Engine"));
                        })
                        .OnClicked_Static(&OnStartAirdEngineClicked)
                    ]
                ]
                + SVerticalBox::Slot()
                .FillHeight(1.0f)
                [
                    SNew(SWebBrowser)
                    .InitialURL(Url)
                    .ShowControls(false)
                    .SupportsTransparency(false)
                ]
            ]
        ];
}
