#pragma once

#include "Commands/AIRDCommandBase.h"

class AIRD_API FSpawnActorCommand : public FAIRDCommandBase
{
public:
    virtual FString GetName() const override;
    virtual bool Execute(const FString& PayloadJson, FString& OutMessage) override;
};
