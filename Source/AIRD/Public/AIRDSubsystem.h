#pragma once

#include "EditorSubsystem.h"
#include "AIRDSubsystem.generated.h"

UCLASS()
class AIRD_API UAIRDSubsystem : public UEditorSubsystem
{
    GENERATED_BODY()

public:
    virtual void Initialize(FSubsystemCollectionBase& Collection) override;
    virtual void Deinitialize() override;

    UFUNCTION(BlueprintCallable, Category = "AIRD")
    FString ExecuteCommand(const FString& CommandName, const FString& PayloadJson);
};
