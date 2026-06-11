<?php
declare(strict_types=1);

namespace Vendor\Faq\Api;

use Vendor\Faq\Api\Data\FaqInterface;

interface FaqRepositoryInterface
{
    public function getById(int $id): FaqInterface;
}
