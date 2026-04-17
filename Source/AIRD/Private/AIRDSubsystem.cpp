#include "AIRDSubsystem.h"

#include "Commands/GenerateBlueprintCommand.h"
#include "Commands/MoveActorCommand.h"
#include "Commands/SpawnActorCommand.h"

void UAIRDSubsystem::Initialize(FSubsystemCollectionBase& Collection)
{
    Super::Initialize(Collection);
}

void UAIRDSubsystem::Deinitialize()
{
    Super::Deinitialize();
}

FString UAIRDSubsystem::ExecuteCommand(const FString& CommandName, const FString& PayloadJson)
{
    TUniquePtr<FAIRDCommandBase> Command;

    if (CommandName.Equals(TEXT("spawn_actor"), ESearchCase::IgnoreCase))
    {
        Command = MakeUnique<FSpawnActorCommand>();
    }
    else if (CommandName.Equals(TEXT("move_actor"), ESearchCase::IgnoreCase))
    {
        Command = MakeUnique<FMoveActorCommand>();
    }
    else if (CommandName.Equals(TEXT("generate_blueprint"), ESearchCase::IgnoreCase))
    {
        Command = MakeUnique<FGenerateBlueprintCommand>();
    }
    else
    {
        return FString::Printf(TEXT("Unknown command: %s"), *CommandName);
    }

    FString Result;
    const bool bOk = Command->Execute(PayloadJson, Result);
    return bOk ? Result : FString::Printf(TEXT("Failed: %s"), *Result);
}
