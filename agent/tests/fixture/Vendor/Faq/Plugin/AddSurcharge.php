<?php
declare(strict_types=1);

namespace Vendor\Faq\Plugin;

use Magento\Catalog\Model\Product;

class AddSurcharge
{
    private const SURCHARGE = 5.0;

    public function afterGetFinalPrice(Product $subject, $result): float
    {
        return (float) $result + self::SURCHARGE;
    }
}