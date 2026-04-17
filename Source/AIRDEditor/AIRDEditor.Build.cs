using UnrealBuildTool;

public class AIRDEditor : ModuleRules
{
    public AIRDEditor(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        CppStandard = CppStandardVersion.Cpp20;

        PublicDependencyModuleNames.AddRange(new[]
        {
            "Core",
            "CoreUObject",
            "Engine",
            "Slate",
            "SlateCore",
            "AIRD"
        });

        PrivateDependencyModuleNames.AddRange(new[]
        {
            "UnrealEd",
            "LevelEditor",
            "ToolMenus",
            "Projects",
            "WebBrowser",
            "WebBrowserWidget",
            "EditorStyle"
        });
    }
}
