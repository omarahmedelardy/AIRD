#include "AIRDBridge.h"

#include "AIRDLog.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetToolsModule.h"
#include "Async/Async.h"
#include "Async/TaskGraphInterfaces.h"
#include "Camera/CameraActor.h"
#include "Editor.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "EdGraph/EdGraph.h"
#include "Engine/DirectionalLight.h"
#include "Engine/PointLight.h"
#include "Engine/Selection.h"
#include "Engine/SpotLight.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/Blueprint.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "HAL/FileManager.h"
#include "IImageWrapper.h"
#include "IImageWrapperModule.h"
#include "IPythonScriptPlugin.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "EdGraphSchema_K2.h"
#include "Misc/Base64.h"
#include "Misc/CoreMisc.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/GarbageCollection.h"
#include "UObject/NoExportTypes.h"
#include "UObject/Package.h"

namespace AIRDInternal
{
    static FString GLastBlueprintEditError = TEXT("none");

    static void SetBlueprintEditError(const FString& ErrorCode, const FString& Detail = FString())
    {
        GLastBlueprintEditError = ErrorCode.IsEmpty() ? TEXT("unknown") : ErrorCode;
        if (!Detail.IsEmpty())
        {
            UE_LOG(LogAIRD, Warning, TEXT("Blueprint edit failed (%s): %s"), *GLastBlueprintEditError, *Detail);
        }
    }

    static void ClearBlueprintEditError()
    {
        GLastBlueprintEditError = TEXT("none");
    }

    static FString GetBlueprintEditError()
    {
        return GLastBlueprintEditError;
    }

    static UWorld* GetEditorWorld()
    {
#if WITH_EDITOR
        if (GEditor)
        {
            return GEditor->GetEditorWorldContext().World();
        }
#endif
        return nullptr;
    }

    static bool IsValidBlueprintIdentifier(const FString& Name)
    {
        if (Name.IsEmpty())
        {
            return false;
        }

        const TCHAR First = Name[0];
        if (!(FChar::IsAlpha(First) || First == TEXT('_')))
        {
            return false;
        }

        for (int32 Index = 1; Index < Name.Len(); ++Index)
        {
            const TCHAR Ch = Name[Index];
            if (!(FChar::IsAlnum(Ch) || Ch == TEXT('_')))
            {
                return false;
            }
        }
        return true;
    }

    static FString NormalizeBlueprintObjectPath(const FString& RawPath)
    {
        FString Path = RawPath;
        Path.TrimStartAndEndInline();
        Path = Path.Replace(TEXT("\""), TEXT(""));

        if (Path.StartsWith(TEXT("Blueprint'"), ESearchCase::IgnoreCase) && Path.EndsWith(TEXT("'")))
        {
            Path = Path.Mid(10, Path.Len() - 11);
            Path.TrimStartAndEndInline();
        }

        Path.RemoveFromEnd(TEXT(" blueprint"), ESearchCase::IgnoreCase);
        Path.TrimStartAndEndInline();

        if (Path.EndsWith(TEXT("_C"), ESearchCase::IgnoreCase))
        {
            Path.LeftChopInline(2);
        }

        if (Path.IsEmpty() || !Path.StartsWith(TEXT("/")))
        {
            return FString();
        }

        if (!Path.Contains(TEXT(".")))
        {
            FString AssetName;
            Path.Split(TEXT("/"), nullptr, &AssetName, ESearchCase::IgnoreCase, ESearchDir::FromEnd);
            AssetName.TrimStartAndEndInline();
            if (AssetName.IsEmpty())
            {
                return FString();
            }
            Path = Path + TEXT(".") + AssetName;
        }

        return Path;
    }

    static UBlueprint* LoadBlueprintForEdit(const FString& RawPath)
    {
        const FString ObjectPath = NormalizeBlueprintObjectPath(RawPath);
        if (ObjectPath.IsEmpty())
        {
            SetBlueprintEditError(TEXT("invalid_blueprint_path"), RawPath);
            return nullptr;
        }

        UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *ObjectPath);
        if (!IsValid(Blueprint))
        {
            SetBlueprintEditError(TEXT("invalid_blueprint_path"), ObjectPath);
            return nullptr;
        }
        return Blueprint;
    }

    static bool IsWorldSafeForReadGameThread(UWorld* World)
    {
        if (!IsInGameThread())
        {
            return false;
        }
        if (IsEngineExitRequested())
        {
            return false;
        }
        if (IsGarbageCollecting())
        {
            return false;
        }
        if (!IsValid(World))
        {
            return false;
        }
        if (World->bIsTearingDown || World->PersistentLevel == nullptr)
        {
            return false;
        }
        if (World->WorldType == EWorldType::None || World->WorldType == EWorldType::Inactive)
        {
            return false;
        }
        return true;
    }

    /** Must run on the game thread only. */
    static TArray<AActor*> GatherAllActorsInWorldGameThread()
    {
        TArray<AActor*> Actors;
        UWorld* World = GetEditorWorld();
        if (!IsWorldSafeForReadGameThread(World))
        {
            return Actors;
        }

        for (TActorIterator<AActor> It(World); It; ++It)
        {
            AActor* Actor = *It;
            if (!IsValid(Actor))
            {
                continue;
            }
            if (Actor->GetWorld() != World)
            {
                continue;
            }
            Actors.Add(Actor);
        }

        return Actors;
    }

    /** Must run on the game thread only. Always returns valid JSON with an "actors" array. */
    static FString BuildActorsJsonGameThread()
    {
        TArray<TSharedPtr<FJsonValue>> ActorValues;
        for (AActor* Actor : GatherAllActorsInWorldGameThread())
        {
            if (!IsValid(Actor))
            {
                continue;
            }

            const FVector Location = Actor->GetActorLocation();
            const FRotator Rotation = Actor->GetActorRotation();
            const FVector Scale = Actor->GetActorScale3D();

            TSharedPtr<FJsonObject> ActorObject = MakeShared<FJsonObject>();
            ActorObject->SetStringField(TEXT("name"), Actor->GetName());
            ActorObject->SetStringField(TEXT("class"), Actor->GetClass()->GetName());

            TSharedPtr<FJsonObject> LocationObject = MakeShared<FJsonObject>();
            LocationObject->SetNumberField(TEXT("x"), Location.X);
            LocationObject->SetNumberField(TEXT("y"), Location.Y);
            LocationObject->SetNumberField(TEXT("z"), Location.Z);
            ActorObject->SetObjectField(TEXT("location"), LocationObject);

            TSharedPtr<FJsonObject> RotationObject = MakeShared<FJsonObject>();
            RotationObject->SetNumberField(TEXT("pitch"), Rotation.Pitch);
            RotationObject->SetNumberField(TEXT("yaw"), Rotation.Yaw);
            RotationObject->SetNumberField(TEXT("roll"), Rotation.Roll);
            ActorObject->SetObjectField(TEXT("rotation"), RotationObject);

            TSharedPtr<FJsonObject> ScaleObject = MakeShared<FJsonObject>();
            ScaleObject->SetNumberField(TEXT("x"), Scale.X);
            ScaleObject->SetNumberField(TEXT("y"), Scale.Y);
            ScaleObject->SetNumberField(TEXT("z"), Scale.Z);
            ActorObject->SetObjectField(TEXT("scale"), ScaleObject);

            ActorValues.Add(MakeShared<FJsonValueObject>(ActorObject));
        }

        TSharedPtr<FJsonObject> Root = MakeShared<FJsonObject>();
        Root->SetArrayField(TEXT("actors"), ActorValues);

        FString OutJson;
        TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&OutJson);
        FJsonSerializer::Serialize(Root.ToSharedRef(), Writer);
        return OutJson;
    }

#if WITH_EDITOR
    static bool ResolveBlueprintVariablePinType(const FString& VariableType, FEdGraphPinType& OutPinType)
    {
        FString Type = VariableType;
        Type.TrimStartAndEndInline();
        Type = Type.ToLower();
        if (Type.IsEmpty())
        {
            Type = TEXT("float");
        }

        OutPinType = FEdGraphPinType();

        if (Type == TEXT("bool") || Type == TEXT("boolean"))
        {
            OutPinType.PinCategory = UEdGraphSchema_K2::PC_Boolean;
            return true;
        }
        if (Type == TEXT("int") || Type == TEXT("int32") || Type == TEXT("integer"))
        {
            OutPinType.PinCategory = UEdGraphSchema_K2::PC_Int;
            return true;
        }
        if (Type == TEXT("int64") || Type == TEXT("long"))
        {
            OutPinType.PinCategory = UEdGraphSchema_K2::PC_Int64;
            return true;
        }
        if (Type == TEXT("float") || Type == TEXT("number"))
        {
            OutPinType.PinCategory = UEdGraphSchema_K2::PC_Real;
            OutPinType.PinSubCategory = UEdGraphSchema_K2::PC_Float;
            return true;
        }
        if (Type == TEXT("double"))
        {
            OutPinType.PinCategory = UEdGraphSchema_K2::PC_Real;
            OutPinType.PinSubCategory = UEdGraphSchema_K2::PC_Double;
            return true;
        }
        if (Type == TEXT("string"))
        {
            OutPinType.PinCategory = UEdGraphSchema_K2::PC_String;
            return true;
        }
        if (Type == TEXT("name"))
        {
            OutPinType.PinCategory = UEdGraphSchema_K2::PC_Name;
            return true;
        }
        if (Type == TEXT("text"))
        {
            OutPinType.PinCategory = UEdGraphSchema_K2::PC_Text;
            return true;
        }
        if (Type == TEXT("vector"))
        {
            OutPinType.PinCategory = UEdGraphSchema_K2::PC_Struct;
            OutPinType.PinSubCategoryObject = TBaseStructure<FVector>::Get();
            return true;
        }
        if (Type == TEXT("rotator"))
        {
            OutPinType.PinCategory = UEdGraphSchema_K2::PC_Struct;
            OutPinType.PinSubCategoryObject = TBaseStructure<FRotator>::Get();
            return true;
        }
        if (Type == TEXT("transform"))
        {
            OutPinType.PinCategory = UEdGraphSchema_K2::PC_Struct;
            OutPinType.PinSubCategoryObject = TBaseStructure<FTransform>::Get();
            return true;
        }
        return false;
    }

    static bool HasFunctionGraphNamed(const UBlueprint* Blueprint, const FName FunctionName)
    {
        if (!IsValid(Blueprint))
        {
            return false;
        }
        for (const UEdGraph* Graph : Blueprint->FunctionGraphs)
        {
            if (IsValid(Graph) && Graph->GetFName() == FunctionName)
            {
                return true;
            }
        }
        return false;
    }

    static bool AddBlueprintVariableGameThread(
        const FString& BlueprintPath,
        const FString& VariableName,
        const FString& VariableType
    )
    {
        if (!IsInGameThread())
        {
            SetBlueprintEditError(TEXT("unsupported"), TEXT("AddBlueprintVariable must run on game thread."));
            return false;
        }

        UBlueprint* Blueprint = LoadBlueprintForEdit(BlueprintPath);
        if (!IsValid(Blueprint))
        {
            return false;
        }

        const FString SanitizedName = VariableName.TrimStartAndEnd();
        if (!IsValidBlueprintIdentifier(SanitizedName))
        {
            SetBlueprintEditError(TEXT("invalid_name"), SanitizedName);
            return false;
        }

        const FName VariableFName(*SanitizedName);
        if (FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, VariableFName) != INDEX_NONE)
        {
            SetBlueprintEditError(TEXT("duplicate_name"), SanitizedName);
            return false;
        }

        FEdGraphPinType PinType;
        if (!ResolveBlueprintVariablePinType(VariableType, PinType))
        {
            SetBlueprintEditError(TEXT("unsupported"), VariableType);
            return false;
        }

        Blueprint->Modify();
        if (!FBlueprintEditorUtils::AddMemberVariable(Blueprint, VariableFName, PinType))
        {
            SetBlueprintEditError(TEXT("unsupported"), TEXT("AddMemberVariable returned false."));
            return false;
        }

        if (FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, VariableFName) == INDEX_NONE)
        {
            SetBlueprintEditError(TEXT("operation_failed"), TEXT("Variable was not persisted in blueprint."));
            return false;
        }

        FKismetEditorUtilities::CompileBlueprint(Blueprint);
        if (Blueprint->Status == BS_Error)
        {
            SetBlueprintEditError(TEXT("compile_failed"), TEXT("Blueprint compile failed after variable add."));
            return false;
        }

        if (UPackage* Package = Blueprint->GetOutermost())
        {
            Package->MarkPackageDirty();
        }

        ClearBlueprintEditError();
        return true;
    }

    static bool AddBlueprintFunctionGameThread(const FString& BlueprintPath, const FString& FunctionName)
    {
        if (!IsInGameThread())
        {
            SetBlueprintEditError(TEXT("unsupported"), TEXT("AddBlueprintFunction must run on game thread."));
            return false;
        }

        UBlueprint* Blueprint = LoadBlueprintForEdit(BlueprintPath);
        if (!IsValid(Blueprint))
        {
            return false;
        }

        const FString SanitizedName = FunctionName.TrimStartAndEnd();
        if (!IsValidBlueprintIdentifier(SanitizedName))
        {
            SetBlueprintEditError(TEXT("invalid_name"), SanitizedName);
            return false;
        }

        const FName FunctionFName(*SanitizedName);
        if (!FBlueprintEditorUtils::IsGraphNameUnique(Blueprint, FunctionFName)
            || HasFunctionGraphNamed(Blueprint, FunctionFName))
        {
            SetBlueprintEditError(TEXT("duplicate_name"), SanitizedName);
            return false;
        }

        Blueprint->Modify();
        UEdGraph* NewGraph = FBlueprintEditorUtils::CreateNewGraph(
            Blueprint,
            FunctionFName,
            UEdGraph::StaticClass(),
            UEdGraphSchema_K2::StaticClass()
        );
        if (!IsValid(NewGraph))
        {
            SetBlueprintEditError(TEXT("unsupported"), TEXT("Could not create function graph."));
            return false;
        }

        FBlueprintEditorUtils::AddFunctionGraph<UClass>(
            Blueprint,
            NewGraph,
            /*bIsUserCreated=*/true,
            /*SignatureFromObject=*/nullptr
        );

        if (!HasFunctionGraphNamed(Blueprint, FunctionFName))
        {
            SetBlueprintEditError(TEXT("operation_failed"), TEXT("Function graph was not added to blueprint."));
            return false;
        }

        FKismetEditorUtilities::CompileBlueprint(Blueprint);
        if (Blueprint->Status == BS_Error)
        {
            SetBlueprintEditError(TEXT("compile_failed"), TEXT("Blueprint compile failed after function add."));
            return false;
        }

        if (UPackage* Package = Blueprint->GetOutermost())
        {
            Package->MarkPackageDirty();
        }

        ClearBlueprintEditError();
        return true;
    }

    /** Must run on the game thread only. */
    static bool CaptureViewportScreenshotGameThread(FString& OutBase64)
    {
        if (!GEditor || !GEditor->GetActiveViewport())
        {
            UE_LOG(LogAIRD, Warning, TEXT("CaptureViewportScreenshot: no active editor viewport."));
            return false;
        }

        FViewport* Viewport = GEditor->GetActiveViewport();
        TArray<FColor> Pixels;
        if (!Viewport->ReadPixels(Pixels))
        {
            UE_LOG(LogAIRD, Warning, TEXT("CaptureViewportScreenshot: ReadPixels failed."));
            return false;
        }

        const FIntPoint Size = Viewport->GetSizeXY();
        if (Size.X <= 0 || Size.Y <= 0)
        {
            UE_LOG(LogAIRD, Warning, TEXT("CaptureViewportScreenshot: invalid viewport size %dx%d."), Size.X, Size.Y);
            return false;
        }

        IImageWrapperModule& ImageWrapperModule = FModuleManager::LoadModuleChecked<IImageWrapperModule>(TEXT("ImageWrapper"));
        TSharedPtr<IImageWrapper> Wrapper = ImageWrapperModule.CreateImageWrapper(EImageFormat::PNG);
        if (!Wrapper.IsValid())
        {
            UE_LOG(LogAIRD, Warning, TEXT("CaptureViewportScreenshot: could not create PNG wrapper."));
            return false;
        }

        if (!Wrapper->SetRaw(Pixels.GetData(), Pixels.Num() * sizeof(FColor), Size.X, Size.Y, ERGBFormat::BGRA, 8))
        {
            UE_LOG(LogAIRD, Warning, TEXT("CaptureViewportScreenshot: SetRaw failed (size mismatch?)."));
            return false;
        }

        const TArray64<uint8>& PngData = Wrapper->GetCompressed(100);
        TArray<uint8> PngData32;
        PngData32.Append(PngData.GetData(), static_cast<int32>(PngData.Num()));
        OutBase64 = FBase64::Encode(PngData32);
        return true;
    }
#endif
}

TArray<AActor*> UAIRDBridge::GetAllActorsInWorld()
{
    if (IsInGameThread())
    {
        return AIRDInternal::GatherAllActorsInWorldGameThread();
    }

    TSharedPtr<TArray<AActor*>> Result = MakeShared<TArray<AActor*>>();
    FGraphEventRef Completion = FFunctionGraphTask::CreateAndDispatchWhenReady(
        [Result]()
        {
            *Result = AIRDInternal::GatherAllActorsInWorldGameThread();
        },
        TStatId(),
        nullptr,
        ENamedThreads::GameThread);
    FTaskGraphInterface::Get().WaitUntilTaskCompletes(Completion);
    return *Result;
}

FString UAIRDBridge::GetActorsAsJSON()
{
    if (IsInGameThread())
    {
        return AIRDInternal::BuildActorsJsonGameThread();
    }

    UE_LOG(LogAIRD, Verbose, TEXT("GetActorsAsJSON: marshaling to GameThread"));

    TSharedPtr<FString> Result = MakeShared<FString>();
    FGraphEventRef Completion = FFunctionGraphTask::CreateAndDispatchWhenReady(
        [Result]()
        {
            *Result = AIRDInternal::BuildActorsJsonGameThread();
        },
        TStatId(),
        nullptr,
        ENamedThreads::GameThread);
    FTaskGraphInterface::Get().WaitUntilTaskCompletes(Completion);
    return *Result;
}

bool UAIRDBridge::CaptureViewportScreenshot(FString& OutBase64)
{
#if WITH_EDITOR
    if (IsInGameThread())
    {
        return AIRDInternal::CaptureViewportScreenshotGameThread(OutBase64);
    }

    struct FViewportCaptureResult
    {
        bool bOk = false;
        FString Base64;
    };
    TSharedPtr<FViewportCaptureResult> Result = MakeShared<FViewportCaptureResult>();
    FGraphEventRef Completion = FFunctionGraphTask::CreateAndDispatchWhenReady(
        [Result]()
        {
            FString Local;
            Result->bOk = AIRDInternal::CaptureViewportScreenshotGameThread(Local);
            Result->Base64 = MoveTemp(Local);
        },
        TStatId(),
        nullptr,
        ENamedThreads::GameThread);
    FTaskGraphInterface::Get().WaitUntilTaskCompletes(Completion);
    OutBase64 = Result->Base64;
    return Result->bOk;
#else
    return false;
#endif
}

bool UAIRDBridge::SpawnActorFromDescription(const FString& Description, FVector Location)
{
    UWorld* World = AIRDInternal::GetEditorWorld();
    if (!World)
    {
        return false;
    }

    const FString D = Description.ToLower();
    auto Contains = [&D](const TCHAR* Token) -> bool { return D.Contains(Token); };

    UClass* SpawnClass = AActor::StaticClass();
    if (Contains(TEXT("camera")))
    {
        SpawnClass = ACameraActor::StaticClass();
    }
    else if (Contains(TEXT("spot")) && Contains(TEXT("light")))
    {
        SpawnClass = ASpotLight::StaticClass();
    }
    else if (Contains(TEXT("directional")) || Contains(TEXT("sun")))
    {
        SpawnClass = ADirectionalLight::StaticClass();
    }
    else if ((Contains(TEXT("point")) && Contains(TEXT("light"))) || (Contains(TEXT("light")) && !Contains(TEXT("mesh"))))
    {
        SpawnClass = APointLight::StaticClass();
    }
    else if (Contains(TEXT("sphere")) || Contains(TEXT("cube")) || Contains(TEXT("box")) || Contains(TEXT("cylinder")) || Contains(TEXT("mesh")))
    {
        SpawnClass = AStaticMeshActor::StaticClass();
    }

    FActorSpawnParameters Params;
    AActor* NewActor = World->SpawnActor<AActor>(SpawnClass, Location, FRotator::ZeroRotator, Params);
    return IsValid(NewActor);
}

bool UAIRDBridge::MoveActorToLocation(AActor* Actor, FVector NewLocation)
{
    if (!IsValid(Actor))
    {
        return false;
    }

    Actor->Modify();
    Actor->SetActorLocation(NewLocation);
    return true;
}

bool UAIRDBridge::GenerateBlueprintFromPrompt(const FString& Prompt)
{
#if WITH_EDITOR
    const FString SafeName = Prompt.IsEmpty() ? TEXT("BP_AIRDGenerated") : Prompt.Replace(TEXT(" "), TEXT("_")).Left(48);
    FString PackageName;
    FString AssetName;

    FAssetToolsModule& AssetToolsModule = FModuleManager::LoadModuleChecked<FAssetToolsModule>(TEXT("AssetTools"));
    AssetToolsModule.Get().CreateUniqueAssetName(TEXT("/Game/AIRD/") + SafeName, TEXT(""), PackageName, AssetName);

    UPackage* Package = CreatePackage(*PackageName);
    if (!Package)
    {
        return false;
    }

    UBlueprint* Blueprint = FKismetEditorUtilities::CreateBlueprint(
        AActor::StaticClass(),
        Package,
        FName(*AssetName),
        EBlueprintType::BPTYPE_Normal,
        UBlueprint::StaticClass(),
        UBlueprintGeneratedClass::StaticClass(),
        TEXT("AIRDBlueprintGenerator")
    );

    if (!Blueprint)
    {
        return false;
    }

    FAssetRegistryModule::AssetCreated(Blueprint);
    Package->MarkPackageDirty();
    return true;
#else
    return false;
#endif
}

bool UAIRDBridge::AddBlueprintVariable(
    const FString& BlueprintPath,
    const FString& VariableName,
    const FString& VariableType
)
{
#if WITH_EDITOR
    if (IsInGameThread())
    {
        return AIRDInternal::AddBlueprintVariableGameThread(BlueprintPath, VariableName, VariableType);
    }

    TSharedPtr<bool> Result = MakeShared<bool>(false);
    FGraphEventRef Completion = FFunctionGraphTask::CreateAndDispatchWhenReady(
        [Result, BlueprintPath, VariableName, VariableType]()
        {
            *Result = AIRDInternal::AddBlueprintVariableGameThread(
                BlueprintPath,
                VariableName,
                VariableType
            );
        },
        TStatId(),
        nullptr,
        ENamedThreads::GameThread);
    FTaskGraphInterface::Get().WaitUntilTaskCompletes(Completion);
    return *Result;
#else
    AIRDInternal::SetBlueprintEditError(
        TEXT("editor_only"),
        TEXT("AddBlueprintVariable is only available in WITH_EDITOR runtime.")
    );
    return false;
#endif
}

bool UAIRDBridge::AddBlueprintFunction(const FString& BlueprintPath, const FString& FunctionName)
{
#if WITH_EDITOR
    if (IsInGameThread())
    {
        return AIRDInternal::AddBlueprintFunctionGameThread(BlueprintPath, FunctionName);
    }

    TSharedPtr<bool> Result = MakeShared<bool>(false);
    FGraphEventRef Completion = FFunctionGraphTask::CreateAndDispatchWhenReady(
        [Result, BlueprintPath, FunctionName]()
        {
            *Result = AIRDInternal::AddBlueprintFunctionGameThread(BlueprintPath, FunctionName);
        },
        TStatId(),
        nullptr,
        ENamedThreads::GameThread);
    FTaskGraphInterface::Get().WaitUntilTaskCompletes(Completion);
    return *Result;
#else
    AIRDInternal::SetBlueprintEditError(
        TEXT("editor_only"),
        TEXT("AddBlueprintFunction is only available in WITH_EDITOR runtime.")
    );
    return false;
#endif
}

FString UAIRDBridge::GetLastBlueprintEditError()
{
    return AIRDInternal::GetBlueprintEditError();
}

bool UAIRDBridge::ExecutePythonCommand(const FString& Command)
{
#if WITH_EDITOR
    const bool bTrustedMcpBootstrap =
        Command.Contains(TEXT("run_mcp_in_unreal.py"), ESearchCase::IgnoreCase)
        || Command.Contains(TEXT("runpy.run_path"), ESearchCase::IgnoreCase);
    const bool bTrustedMcpControl =
        Command.Contains(TEXT("mcp_server.stop_mcp_server"), ESearchCase::IgnoreCase)
        || Command.Contains(TEXT("mcp_server.is_mcp_running"), ESearchCase::IgnoreCase)
        || Command.Contains(TEXT("mcp_server.update_scene_context_async"), ESearchCase::IgnoreCase);
    if (!bTrustedMcpBootstrap && !bTrustedMcpControl)
    {
        UE_LOG(LogAIRD, Warning, TEXT("ExecutePythonCommand blocked (command is not trusted MCP control/bootstrap)."));
        return false;
    }

    if (IPythonScriptPlugin* PythonModule = FModuleManager::GetModulePtr<IPythonScriptPlugin>(TEXT("PythonScriptPlugin")))
    {
        return PythonModule->ExecPythonCommand(*Command);
    }
#endif
    return false;
}
