using UnrealBuildTool;

public class AIRD : ModuleRules
{
    public AIRD(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        CppStandard = CppStandardVersion.Cpp20;

        PublicDependencyModuleNames.AddRange(new[]
        {
            "Core",
            "CoreUObject",
            "Engine",
            "Json",
            "JsonUtilities"
        });

        PrivateDependencyModuleNames.AddRange(new[]
        {
            "UnrealEd",
            "EditorSubsystem",
            "LevelEditor",
            "AssetTools",
            "AssetRegistry",
            "Kismet",
            "KismetCompiler",
            "BlueprintGraph",
            "Projects",
            "PythonScriptPlugin",
            "ImageWrapper",
            "Sockets",
            "Slate",
            "SlateCore",
            "WebBrowserWidget"
        });
    }
}
