#include "AIRDEditor.h"

#include "Framework/Docking/TabManager.h"
#include "Framework/MultiBox/MultiBoxBuilder.h"
#include "LevelEditor.h"
#include "Styling/AppStyle.h"
#include "ToolMenus.h"
#include "Widgets/Docking/SDockTab.h"

#define LOCTEXT_NAMESPACE "FAIRDEditorModule"

static const FName AIRDTabName(TEXT("AIRDMainTab"));

TSharedRef<SDockTab> SpawnAIRDWidgetTab(const FSpawnTabArgs& Args);

void FAIRDEditorModule::StartupModule()
{
    FGlobalTabmanager::Get()->RegisterNomadTabSpawner(
        AIRDTabName,
        FOnSpawnTab::CreateStatic(&SpawnAIRDWidgetTab))
        .SetDisplayName(LOCTEXT("AIRDTabTitle", "AIRD"))
        .SetTooltipText(LOCTEXT("AIRDTabTooltip", "Open AIRD Assistant"))
        .SetMenuType(ETabSpawnerMenuType::Hidden);

    UToolMenus::RegisterStartupCallback(
        FSimpleMulticastDelegate::FDelegate::CreateRaw(this, &FAIRDEditorModule::RegisterMenus));
}

void FAIRDEditorModule::ShutdownModule()
{
    UnregisterMenus();

    if (FModuleManager::Get().IsModuleLoaded("LevelEditor"))
    {
        FGlobalTabmanager::Get()->UnregisterNomadTabSpawner(AIRDTabName);
    }
}

void FAIRDEditorModule::RegisterMenus()
{
    FToolMenuOwnerScoped OwnerScoped(this);
    UToolMenu* Menu = UToolMenus::Get()->ExtendMenu("LevelEditor.MainMenu.Window");
    FToolMenuSection& Section = Menu->FindOrAddSection("WindowLayout");

    Section.AddMenuEntry(
        "OpenAIRDWindow",
        LOCTEXT("OpenAIRDWindow_Label", "AIRD"),
        LOCTEXT("OpenAIRDWindow_Tooltip", "Open AIRD window"),
        FSlateIcon(FAppStyle::GetAppStyleSetName(), "LevelEditor.Tabs.Details"),
        FUIAction(FExecuteAction::CreateLambda([]
        {
            FGlobalTabmanager::Get()->TryInvokeTab(AIRDTabName);
        })));
}

void FAIRDEditorModule::UnregisterMenus()
{
    if (UToolMenus::TryGet())
    {
        UToolMenus::UnregisterOwner(this);
    }
}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FAIRDEditorModule, AIRDEditor)
